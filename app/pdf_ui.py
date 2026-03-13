import discord
import logging
from . import config
import tempfile
import urllib.request
import os

logger = logging.getLogger(__name__)

async def process_pdf(prompt_focus: str, bot, channel, attachment, author, message_id, interaction: discord.Interaction):
    """Downloads the PDF, extracts text, calls LangExtract with the focus, and saves it."""
    try:
        if interaction.response.is_done():
            progress_msg = await interaction.followup.send("⏳ Fetching and parsing PDF (Raw Text Extraction)...")
        else:
            await interaction.response.send_message("⏳ Fetching and parsing PDF (Raw Text Extraction)...")
            progress_msg = await interaction.original_response()

        import langextract as lx
        from langextract.core.data import ExampleData, Extraction
        import fitz  # PyMuPDF

        fd, temp_pdf = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)

        try:
            await attachment.save(temp_pdf)

            # Read Raw Txt
            doc = fitz.open(temp_pdf)
            raw_text = ""
            for page in doc:
                raw_text += page.get_text()
            doc.close()

            await progress_msg.edit(content=f"⏳ Extracting structured insights via LangExtract (Model: `{config.LANGEXTRACT_MODEL}`)...")

            base_prompt = f"Extract the key information from this document focusing strictly on: {prompt_focus}. "
            base_prompt += "Discard irrelevant filler. Output the exact facts, entities, and actions."

            # Determine dynamic examples based on the selected focus string
            examples_to_use = []
            if "Action Items" in prompt_focus:
                examples_to_use.append(ExampleData(
                    text="Meeting notes: Alice needs to finalize the Q3 report by Friday. Bob will email the client tomorrow.",
                    extractions=[
                        Extraction(extraction_class="Task", extraction_text="finalize the Q3 report", attributes={"Assignee": "Alice", "Deadline": "Friday"}),
                        Extraction(extraction_class="Task", extraction_text="email the client", attributes={"Assignee": "Bob", "Deadline": "tomorrow"})
                    ]
                ))
            elif "Financial" in prompt_focus or "Cost" in prompt_focus:
                examples_to_use.append(ExampleData(
                    text="Uber ride to the airport on Oct 12th. Total was $45.20 including a $5 tip.",
                    extractions=[
                        Extraction(extraction_class="Vendor", extraction_text="Uber"),
                        Extraction(extraction_class="Date", extraction_text="Oct 12th"),
                        Extraction(extraction_class="Total Cost", extraction_text="$45.20"),
                        Extraction(extraction_class="Item", extraction_text="ride to the airport"),
                    ]
                ))
            elif "Technical" in prompt_focus or "Algorithm" in prompt_focus:
                examples_to_use.append(ExampleData(
                    text="We migrated the backend to use Node.js and PostgreSQL. The sorting uses a custom Quicksort algorithm.",
                    extractions=[
                        Extraction(extraction_class="Technology", extraction_text="Node.js"),
                        Extraction(extraction_class="Technology", extraction_text="PostgreSQL"),
                        Extraction(extraction_class="Algorithm", extraction_text="Quicksort"),
                        Extraction(extraction_class="Architecture Decision", extraction_text="migrated the backend"),
                    ]
                ))
            else:
                # Default / General / Custom Focus Fallback
                examples_to_use.append(ExampleData(
                    text="The new Quantum model releases next Tuesday on March 14th in New York. It features a 50% speed increase.",
                    extractions=[
                        Extraction(extraction_class="Entity", extraction_text="Quantum model"),
                        Extraction(extraction_class="Date", extraction_text="March 14th", attributes={"Context": "Release Date"}),
                        Extraction(extraction_class="Location", extraction_text="New York"),
                        Extraction(extraction_class="Key Fact", extraction_text="50% speed increase"),
                    ]
                ))

            kwargs = {
                "text_or_documents": raw_text[:100000],  # LangExtract cap to be safe
                "prompt_description": base_prompt,
                "examples": examples_to_use
            }

            # Strictly enforce the Gemini provider to prevent auto-routing to Ollama
            kwargs["config"] = lx.factory.ModelConfig(
                model_id=config.LANGEXTRACT_MODEL,
                provider="gemini",
                provider_kwargs={"api_key": config.GOOGLE_API_KEY}
            )

            # LangExtract Bug Fix: When provider="gemini" is explicitly passed,
            # it skips initializing the factory registry. We must manually load builtins.
            lx.providers.load_builtins_once()
            lx.providers.load_plugins_once()

            import asyncio
            result = await asyncio.to_thread(lx.extract, **kwargs)

            if not result.extractions:
                 await progress_msg.edit(content="❌ Langextract found nothing matching your criteria.")
                 return

            # Format the output for the daily capture
            formatted_capture = f"**Document Extraction:** `{attachment.filename}`\n**Focus:** {prompt_focus}\n\n"
            for ext in result.extractions:
                formatted_capture += f"- **[{ext.extraction_class.upper()}]** {ext.extraction_text}\n"
                if ext.attributes:
                     for k, v in ext.attributes.items():
                         formatted_capture += f"  - `{k}`: {v}\n"

            await progress_msg.edit(content=f"✅ Successfully converted `{attachment.filename}` down to specific insights!\nSaving to Daily Capture...")

            # Seamlessly funnel the condensed insight into the standard capture pipeline
            await bot._handle_capture(channel, formatted_capture, author, message_id)

        finally:
            if os.path.exists(temp_pdf): os.remove(temp_pdf)

    except ImportError as e:
        await interaction.followup.send(f"❌ Missing library for PDF processing: {e}. Need `langextract` and `PyMuPDF`.")
    except Exception as e:
        logger.error(f"PDF Extraction failed: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Failed to extract PDF: {e}")


class CustomFocusModal(discord.ui.Modal, title='Custom PDF Extraction Focus'):
    focus_input = discord.ui.TextInput(
        label='What exactly should I look for?',
        style=discord.TextStyle.paragraph,
        placeholder='e.g., "Find all cardiovascular disease risks, medications prescribed, and dates of appointments."',
        required=True,
        max_length=500
    )

    def __init__(self, bot, channel, attachment, author, message_id):
        super().__init__()
        self.bot = bot
        self.channel = channel
        self.attachment = attachment
        self.author = author
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await process_pdf(self.focus_input.value, self.bot, self.channel, self.attachment, self.author, self.message_id, interaction)


class PDFOptionsView(discord.ui.View):
    def __init__(self, bot, channel, attachment, author, message_id):
        super().__init__(timeout=3600)
        self.bot = bot
        self.channel = channel
        self.attachment = attachment
        self.author = author
        self.message_id = message_id

    @discord.ui.select(
        placeholder="Select a predefined extraction focus...",
        options=[
            discord.SelectOption(label="General Summary & Entities", description="Who, what, when, where, and a quick summary.", value="Summary, Core Entities, Main Takeaways"),
            discord.SelectOption(label="Action Items & Tasks", description="Find tasks, homework, or things to do.", value="Action Items, Tasks, Deadlines, Responsibilities"),
            discord.SelectOption(label="Financial / Receipts", description="Extract costs, totals, dates, and vendors.", value="Total Cost, Vendor, Date, Items Purchased, Tax"),
            discord.SelectOption(label="Technical / Code", description="Extract architecture decisions, algorithms, and dependencies.", value="Algorithms, Technologies Used, Architecture Decisions, Code Snippets"),
        ]
    )
    async def select_focus(self, interaction: discord.Interaction, select: discord.ui.Select):
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await process_pdf(select.values[0], self.bot, self.channel, self.attachment, self.author, self.message_id, interaction)

    @discord.ui.button(label="Provide Custom Focus", style=discord.ButtonStyle.primary, row=1)
    async def custom_focus_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        modal = CustomFocusModal(self.bot, self.channel, self.attachment, self.author, self.message_id)
        await interaction.response.send_modal(modal)
