"""
Index markdown and text files into ChromaDB for RAG.
Reads from configured knowledge directories, chunks files,
generates embeddings via the inference endpoint, and stores in ChromaDB.

Usage:
    python scripts/index-knowledge.py
    python scripts/index-knowledge.py --folder /path/to/docs --zone library

Environment variables:
    EMBEDDING_URL   - Ollama/vLLM endpoint (default: http://localhost:11434)
    EMBEDDING_MODEL - Model for embeddings (default: nomic-embed-text)
    CHROMA_HOST     - ChromaDB host (default: localhost)
    CHROMA_PORT     - ChromaDB port (default: 8000)
"""
import os
import sys
import argparse

import httpx
import chromadb

# Config from environment
EMBEDDING_URL = os.environ.get("EMBEDDING_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))

SUPPORTED_EXTENSIONS = (".md", ".txt", ".rst")
CHUNK_SIZE = 400  # words per chunk
CHUNK_OVERLAP = 50  # word overlap between chunks


def get_chroma_client():
    return chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)


def chunk_file(filepath: str) -> list[str]:
    """Split a file into overlapping word chunks."""
    with open(filepath, encoding="utf-8", errors="replace") as f:
        words = f.read().split()
    if not words:
        return []
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + CHUNK_SIZE])
        chunks.append(chunk)
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def embed(text: str) -> list[float]:
    """Get embedding vector from the inference endpoint."""
    r = httpx.post(
        f"{EMBEDDING_URL}/api/embed",
        json={"model": EMBEDDING_MODEL, "input": text},
        timeout=120
    )
    r.raise_for_status()
    return r.json()["embeddings"][0]


def index_folder(folder: str, zone: str, collection):
    """Index all supported files in a folder into ChromaDB."""
    indexed = 0
    for root, _, files in os.walk(folder):
        for fname in files:
            if not fname.endswith(SUPPORTED_EXTENSIONS):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, folder)
            chunks = chunk_file(fpath)
            if not chunks:
                continue
            for i, chunk in enumerate(chunks):
                doc_id = f"{zone}-{rel}-{i}"
                try:
                    emb = embed(chunk)
                    collection.upsert(
                        ids=[doc_id],
                        documents=[chunk],
                        embeddings=[emb],
                        metadatas=[{"source": rel, "zone": zone, "chunk": i}]
                    )
                except Exception as e:
                    print(f"  ERROR embedding {rel} chunk {i}: {e}")
                    continue
            print(f"  {rel}: {len(chunks)} chunks indexed")
            indexed += len(chunks)
    return indexed


def main():
    parser = argparse.ArgumentParser(description="Index knowledge into ChromaDB")
    parser.add_argument("--folder", default=os.environ.get("KNOWLEDGE_DIRS", "/library"),
                        help="Folder to index")
    parser.add_argument("--zone", default=os.environ.get("KNOWLEDGE_ZONE", "library"),
                        help="Data zone label (library or vault)")
    parser.add_argument("--collection", default="knowledge",
                        help="ChromaDB collection name")
    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        print(f"ERROR: Folder not found: {args.folder}")
        sys.exit(1)

    print(f"Connecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}...")
    client = get_chroma_client()
    collection = client.get_or_create_collection(
        args.collection,
        metadata={"hnsw:space": "cosine"}
    )

    print(f"Indexing {args.folder} as zone '{args.zone}'...")
    total = index_folder(args.folder, args.zone, collection)
    print(f"Done. {total} chunks indexed into collection '{args.collection}'.")


if __name__ == "__main__":
    main()
