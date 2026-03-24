from fastapi import FastAPI
from pydantic import BaseModel
import chromadb

app = FastAPI()
client = chromadb.PersistentClient(path="./chroma_data")
collection = client.get_or_create_collection("test")

class QueryRequest(BaseModel):
    question: str
    n_results: int = 3

@app.get("/")
def health_check():
    return {"status": "ok"}

@app.post("/search")
def search(req: QueryRequest):
    results = collection.query(
        query_texts=[req.question],
        n_results=req.n_results
    )
    return {
        "question": req.question,
        "documents": results["documents"][0],
        "distances": results["distances"][0]
    }

