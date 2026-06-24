import asyncio
from src.filtering.embedding_engine import EmbeddingEngine
from src.config import get_settings

async def main():
    try:
        print("Settings:", get_settings())
        engine = EmbeddingEngine()
        print("Calling get_embedding...")
        emb = await engine.get_embedding("Test resume text")
        print("Success! Embedding length:", len(emb))
    except Exception as e:
        print("FAILED with error:", repr(e))

if __name__ == "__main__":
    asyncio.run(main())
