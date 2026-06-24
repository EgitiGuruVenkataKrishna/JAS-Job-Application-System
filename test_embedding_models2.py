import asyncio
from google import genai
from google.genai import types
from src.config import get_settings

async def main():
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    
    print("\nTrying text-embedding-004...")
    try:
        response = await client.aio.models.embed_content(
            model="text-embedding-004",
            contents="Hello world!"
        )
        emb = response.embeddings[0].values
        print("Success text-embedding-004! Dim:", len(emb))
    except Exception as e:
        print("Failed text-embedding-004:", e)
        
    print("\nTrying gemini-embedding-2 with 768 dims...")
    try:
        response = await client.aio.models.embed_content(
            model="gemini-embedding-2",
            contents="Hello world!",
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        emb = response.embeddings[0].values
        print("Success gemini-embedding-2! Dim:", len(emb))
    except Exception as e:
        print("Failed gemini-embedding-2:", e)

    print("\nTrying text-embedding-004 via generateContent? No, it's embed_content")

if __name__ == "__main__":
    asyncio.run(main())
