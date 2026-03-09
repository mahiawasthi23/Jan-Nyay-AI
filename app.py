from flask import Flask, render_template, url_for, session, redirect, request, jsonify
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import os
import easyocr
import re
import fitz 
from groq import Groq 
from werkzeug.utils import secure_filename
from PIL import Image
import asyncio
import edge_tts
import chromadb
from sentence_transformers import SentenceTransformer

# --- DATABASE IMPORTS ---
from models import db, User, ChatSession, Message 

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "jan_nyay_key_123")

# --- GROQ CLIENT SETUP ---
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# --- DATABASE CONFIG ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///jannyay.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# --- FOLDERS CONFIG ---
UPLOAD_FOLDER = 'uploads'
AUDIO_FOLDER = 'static/audio'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['AUDIO_FOLDER'] = AUDIO_FOLDER

for folder in [UPLOAD_FOLDER, AUDIO_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# 3. OCR & RAG Initialization
reader = easyocr.Reader(['hi', 'en'], gpu=False)
embed_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
chroma_client = chromadb.PersistentClient(path="./legal_db")
collection = chroma_client.get_or_create_collection(name="indian_laws")

# --- AUTO-CREATE DATABASE TABLES ---
with app.app_context():
    db.create_all()

# --- HELPER FUNCTIONS ---

def mask_sensitive_data(text):
    """Privacy Masking: Improved for Aadhar, Phone, and Email"""
    text = re.sub(r'\b\d{4}[ -]?\d{4}[ -]?(\d{4})\b', r'XXXX-XXXX-\1', text)
    text = re.sub(r'(\+?\d{1,3}[- ]?)?\d{6}(\d{4})', r'\1XXXXXX\2', text)
    text = re.sub(r'\b([a-zA-Z0-9._%+-])[a-zA-Z0-9._%+-]+@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b', r'\1***@\2', text)
    return text

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ['png', 'jpg', 'jpeg', 'pdf']

def get_legal_context(query_text):
    try:
        query_embed = embed_model.encode(query_text[:300]).tolist()
        results = collection.query(query_embeddings=[query_embed], n_results=1)
        return " ".join(results['documents'][0]) if results['documents'] else "No specific sections found."
    except Exception as e:
        print(f"RAG Error: {e}")
        return "Legal database is busy."

async def generate_tts(text, filename, language):
    voice = "hi-IN-MadhurNeural" if language == 'Hindi' else "en-US-GuyNeural"
    clean_text = text.replace("**", "").replace("#", "").replace("*", "")[:500]
    communicate = edge_tts.Communicate(clean_text, voice)
    await communicate.save(os.path.join(app.config['AUDIO_FOLDER'], filename))

# 4. Google OAuth Setup
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

# --- ROUTES ---

@app.route('/')
def index():
    if 'user' in session: return redirect('/dashboard')
    return render_template('login.html')

@app.route('/login')
def login():
    return google.authorize_redirect(url_for('auth', _external=True))

@app.route('/auth/callback')
def auth():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    if user_info: 
        session['user'] = user_info
        user = User.query.filter_by(google_id=user_info['sub']).first()
        if not user:
            new_user = User(google_id=user_info['sub'], name=user_info['name'], email=user_info['email'])
            db.session.add(new_user)
            db.session.commit()
    return redirect('/dashboard')

@app.route('/dashboard')
def dashboard():
    if 'user' in session: 
        user_id = session['user']['sub']
        history = ChatSession.query.filter_by(user_id=user_id).order_by(ChatSession.created_at.desc()).all()
        return render_template('dashboard.html', user_info=session['user'], history=history)
    return redirect('/')

@app.route('/new_chat')
def new_chat():
    session.pop('active_session_id', None)
    session.pop('extracted_text', None)
    return redirect('/dashboard')

# --- MAIN RAG UPLOAD LOGIC (IMAGE & PDF) ---
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return jsonify({"error": "No file"}), 400
    
    file = request.files['file']
    selected_lang = request.form.get('language', 'Hindi')

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[1].lower()
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        
        try:
            raw_text = ""
            
            # 1. Check if PDF
            if ext == 'pdf':
                doc = fitz.open(path)
                for page in doc:
                    raw_text += page.get_text()
                doc.close()
                
                # Agar PDF image-based hai (khali hai), toh EasyOCR fallback use kar sakte hain
                if len(raw_text.strip()) < 10:
                    # Logic for PDF-to-Image OCR can be added here if needed
                    raw_text = "PDF content could not be read. Please ensure it is not a protected or empty scan."
            
            # 2. Check if Image
            else:
                with Image.open(path) as img:
                    img = img.convert('RGB')
                    img.save(path)
                
                results = reader.readtext(path, detail=0)
                raw_text = " ".join(results)
            
            # --- PRIVACY MASKING APPLIED ---
            text = mask_sensitive_data(raw_text)
            session['extracted_text'] = text 

            legal_laws = get_legal_context(text)

            # --- ORIGINAL PROMPT (UNCHANGED) ---
            prompt = f"""
            You are a Legal Expert. Your task is to analyze the UPLOADED DOCUMENT provided below.
            
            STRICT RULE: You MUST provide the entire response ONLY in {selected_lang}. 
            Even if the document is in another language, translate your analysis to {selected_lang}.

            UPLOADED DOCUMENT TEXT: {text}
            LEGAL REFERENCE (BNS/IPC): {legal_laws}
            
            STRICT RULES:
            1. Summarize ONLY the 'UPLOADED DOCUMENT TEXT' in {selected_lang}. Do not summarize the whole BNS Act.
            2. Check if the 'UPLOADED DOCUMENT' violates any 'LEGAL REFERENCE' laws.
            3. If it's a simple certificate or non-legal document, say "This is a non-legal document" in {selected_lang} in the Alerts.
            
            FORMAT (Respond only in {selected_lang}):
            - SUMMARY: (Brief description)
            - RED ALERTS: (Risks or "No legal risks found")
            - NEXT STEPS: (Advice)
            """
            
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}]
            )
            analysis = completion.choices[0].message.content

            audio_name = f"voice_{session['user']['sub']}.mp3"
            asyncio.run(generate_tts(analysis, audio_name, selected_lang))

            # --- SAVE ANALYSIS & UPDATE SESSION TITLE ---
            user_id = session['user']['sub']
            active_sid = session.get('active_session_id')
            
            if not active_sid:
                new_session = ChatSession(user_id=user_id, title=f"Doc: {filename}", document_text=text, analysis_result=analysis)
                db.session.add(new_session)
                db.session.commit()
                session['active_session_id'] = new_session.id
            else:
                chat_session = db.session.get(ChatSession, active_sid)
                if chat_session:
                    chat_session.document_text = text
                    chat_session.analysis_result = analysis
                    chat_session.title = f"Doc: {filename}"
                    db.session.commit()

            return jsonify({
                "message": "Success", 
                "analysis": analysis,
                "doc_name": filename,
                "audio_url": f"/static/audio/{audio_name}?v={os.urandom(4).hex()}"
            })
            
        except Exception as e:
            print(f"Upload Error: {e}")
            return jsonify({"error": str(e)}), 500
            
    return jsonify({"error": "Invalid file"}), 400

@app.route('/chat', methods=['POST'])
def chat():
    if 'user' not in session: return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    user_msg = data.get('message')
    selected_lang = data.get('language', 'English') 
    doc_context = session.get('extracted_text', 'No document.')
    user_id = session['user']['sub']

    chat_sid = session.get('active_session_id')
    if not chat_sid:
        new_session = ChatSession(user_id=user_id, title=user_msg[:30] + "...", document_text=doc_context)
        db.session.add(new_session)
        db.session.commit()
        chat_sid = new_session.id
        session['active_session_id'] = chat_sid

    try:
        laws = get_legal_context(user_msg)
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"Legal AI. Language: {selected_lang}. Doc Context: {doc_context[:1000]}. Laws: {laws}"},
                {"role": "user", "content": user_msg}
            ]
        )
        ai_msg = completion.choices[0].message.content
        
        db.session.add(Message(session_id=chat_sid, role='user', content=user_msg))
        db.session.add(Message(session_id=chat_sid, role='ai', content=ai_msg))
        db.session.commit()
        return jsonify({"response": ai_msg})
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({"error": "AI is busy, please try again."}), 500

@app.route('/get_chat_history/<int:session_id>')
def get_chat_history(session_id):
    if 'user' not in session: return jsonify({"error": "Unauthorized"}), 401
    chat_session = db.session.get(ChatSession, session_id)
    session['active_session_id'] = session_id
    
    # Context restore for RAG
    if chat_session and chat_session.document_text:
        session['extracted_text'] = chat_session.document_text
        
    messages = Message.query.filter_by(session_id=session_id).order_by(Message.timestamp.asc()).all()
    
    return jsonify({
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "analysis": chat_session.analysis_result if chat_session else None,
        "doc_title": chat_session.title if chat_session else "Chat History"
    })

@app.route('/delete_chat/<int:session_id>', methods=['DELETE'])
def delete_chat(session_id):
    if 'user' not in session: return jsonify({"error": "Unauthorized"}), 401
    Message.query.filter_by(session_id=session_id).delete()
    session_to_delete = db.session.get(ChatSession, session_id)
    if session_to_delete:
        db.session.delete(session_to_delete)
        db.session.commit()
        return jsonify({"message": "Success"})
    return jsonify({"error": "Not found"}), 404

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)