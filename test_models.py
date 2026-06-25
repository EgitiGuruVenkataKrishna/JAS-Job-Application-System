from google import genai

from src.config import get_settings


def main():
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)

    print("Available models containing 'text' or 'embed':")
    for model in client.models.list():
        name = model.name.lower()
        if "embed" in name or "text" in name:
            print(f"- {model.name}")

if __name__ == "__main__":
    main()
