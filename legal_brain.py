import os
import chromadb
from sentence_transformers import SentenceTransformer
from PyPDF2 import PdfReader

# 1. Setup ChromaDB & AI Model
chroma_client = chromadb.PersistentClient(path="./legal_db")
model = SentenceTransformer('all-MiniLM-L6-v2')
collection = chroma_client.get_or_create_collection(name="indian_laws")

def ingest_pdf(file_path):
    print(f"📖 Reading PDF: {file_path}...")
    reader = PdfReader(file_path)
    
    # Har page ko alag-alag kanoon ki tarah treat karenge
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text.strip():
            # Kanoon ko AI ki bhasha (Vector) mein badlo
            vector = model.encode(text).tolist()
            
            # Database mein save karo
            collection.add(
                documents=[text],
                embeddings=[vector],
                metadatas={"source": os.path.basename(file_path), "page": i+1},
                ids=[f"{os.path.basename(file_path)}_page_{i+1}"]
            )
    print(f"✅ Successfully added {len(reader.pages)} pages to RAG!")

if __name__ == "__main__":
    # Agar aapke paas 'laws_pdf' folder mein koi PDF hai, toh ye use utha lega
    pdf_folder = "./laws_pdf"
    if not os.path.exists(pdf_folder):
        os.makedirs(pdf_folder)
        print("📁 Please put your Law PDFs in 'laws_pdf' folder and run again.")
    else:
        for file in os.listdir(pdf_folder):
            if file.endswith(".pdf"):
                ingest_pdf(os.path.join(pdf_folder, file))