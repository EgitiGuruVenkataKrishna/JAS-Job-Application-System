import asyncio
from google import genai
from src.config import get_settings

async def main():
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    
    print("Trying gemini-embedding-2...")
    try:
        response = await client.aio.models.embed_content(
            model="gemini-embedding-2",
            contents="Hello world!"
        )
        emb = response.embeddings[0].values
        print("Success gemini-embedding-2! Dim:", len(emb))
    except Exception as e:
        print("Failed:", e)
        
    print("\nTrying text-embedding-004...")
    try:
        response = await client.aio.models.embed_content(
            model="text-embedding-004",
            contents="Hello world!"
        )
        emb = response.embeddings[0].values
        print("Success text-embedding-004! Dim:", len(emb))
    except Exception as e:
        print("Failed:", e)

if __name__ == "__main__":
    asyncio.run(main())
