from dotenv import load_dotenv
import os
from pathlib import Path
load_dotenv() 
base_dir=Path(__file__).resolve().parent
GROQ_API_KEY :str | None = os.getenv('GROQ_API_KEY')
LLM_MODEL :str = 'llama-3.1-8b-instant'
EMBEDDING_MODEL :str = 'all-MiniLM-L6-v2'
_default_pdf = Path(r"C:\Users\zaoui\Downloads\CV - ZAOUI ANASS.pdf")
PDF_PATH :str = os.getenv('PDF_PATH', str(_default_pdf))
MIN_MATCH_SCORE: int = int(os.getenv("MIN_MATCH_SCORE", "65"))
TOP_K :int = 3
LLM_TEMPERATURE :float = 0.1
CHUNK_SIZE :int = 600
CHUNK_OVERLAP :int = 120
FAISS_INDEX_PATH :str = str(base_dir / 'faiss_index')
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# SMTP / candidatures automatiques
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "zaouiazaziop@gmail.com")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM: str = os.getenv("SMTP_FROM", SMTP_USER)
AUTO_APPLY_SEND: bool = os.getenv("AUTO_APPLY_SEND", "false").lower() in ("1", "true", "yes")
AUTO_APPLY_MAX: int = int(os.getenv("AUTO_APPLY_MAX", "15"))
AUTO_APPLY_DELAY_SEC: int = int(os.getenv("AUTO_APPLY_DELAY_SEC", "25"))
MIN_APPLY_SCORE: int = int(os.getenv("MIN_APPLY_SCORE", "80"))
# true = envoie aussi les mails "suggérés" (plus de candidatures ; bounces → Telegram)
AUTO_APPLY_ALLOW_SUGGESTED: bool = os.getenv(
    "AUTO_APPLY_ALLOW_SUGGESTED", "true"
).lower() in ("1", "true", "yes")
APPLICATIONS_SENT_PATH: str = str(base_dir / "applications_sent.json")
COVER_LETTERS_DIR: str = str(base_dir / "cover_letters")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
IMAP_HOST: str = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT: int = int(os.getenv("IMAP_PORT", "993"))