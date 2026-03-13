import os
import sys
import argparse
import textwrap

def main():
    parser = argparse.ArgumentParser(description="Test LangExtract on a sample text or PDF.")
    parser.add_argument("--pdf", type=str, help="Path to a PDF file to test.")
    parser.add_argument("--model", type=str, default="gemini-2.5-flash", help="Model to use (e.g. gemini-2.5-flash, or gemma2:27b for ollama).")
    parser.add_argument("--ollama", action="store_true", help="Set this flag if using Ollama (local execution).")

    args = parser.parse_args()

    try:
        import langextract as lx
    except ImportError:
        print("Error: langextract is not installed. Run 'pip install langextract'")
        sys.exit(1)

    text_input = ""
    if args.pdf:
        print(f"Reading PDF: {args.pdf}")
        try:
            import fitz # PyMuPDF
            doc = fitz.open(args.pdf)
            for page in doc:
                text_input += page.get_text()
            print(f"Loaded {len(text_input)} characters from the PDF.")
        except ImportError:
            print("Error: PyMuPDF is not installed. Run 'pip install pymupdf'")
            sys.exit(1)
    else:
        text_input = "The patient, John Doe, was prescribed 50mg of Metroprolol daily for his hypertension. He should avoid grapefruit."
        print(f"Using default sample text: '{text_input}'")

    prompt = textwrap.dedent("""\
    Extract the key information from this document.
    Identify the main entities, concepts, and actionable items.
    Ensure that the extracted text directly matches the document when possible.""")

    examples = [
        lx.data.ExampleData(
            text="The AI agent was designed by Jane Smith to automate daily tasks like sorting emails and scraping websites.",
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Jane Smith",
                    attributes={"role": "designer"}
                ),
                lx.data.Extraction(
                    extraction_class="action_item",
                    extraction_text="sorting emails",
                    attributes={"context": "automated task"}
                )
            ]
        )
    ]

    print(f"\nRunning extraction with model: {args.model}...")

    kwargs = {
        "text_or_documents": text_input,
        "prompt_description": prompt,
        "examples": examples,
        "model_id": args.model,
    }

    if args.ollama:
        kwargs["model_url"] = "http://localhost:11434"
        kwargs["fence_output"] = False
        kwargs["use_schema_constraints"] = False

    try:
        result = lx.extract(**kwargs)
        print("\n--- EXTRACTIONS ---")
        for ext in result.extractions:
            print(f"[{ext.extraction_class.upper()}] {ext.extraction_text}")
            if ext.attributes:
                for k, v in ext.attributes.items():
                    print(f"  - {k}: {v}")
    except Exception as e:
        print(f"\nExtraction failed: {e}")

if __name__ == "__main__":
    main()
