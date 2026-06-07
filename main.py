"""
=============================================================
  TKA Made Easy — UTBK Tryout Backend
  Framework : FastAPI
  Database  : PostgreSQL (Supabase) via pg8000 (pure Python)
=============================================================
"""

import os
import ssl
import json
import urllib.request
from urllib.parse import urlparse
from typing import Optional

import pg8000
import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Catatan skema tabel "MAPEL MATA PELIA"
# Kolom: mata_pelajaran (String), topic (String), content (Text)
# - topic berisi nama Bab materi ATAU string literal 'soal'
# - content berisi teks materi ATAU teks soal lengkap
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Load environment variables dari file .env
# ---------------------------------------------------------------------------

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL tidak ditemukan. Pastikan file .env sudah dibuat.")

GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------------------------
# Groq Helper
# ---------------------------------------------------------------------------

def call_groq(system_prompt: str, user_prompt: str, model: str = "llama-3.3-70b-versatile") -> str:
    """Kirim prompt ke Groq REST API pakai urllib (pure Python, no library)."""
    if not GROQ_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY tidak ditemukan. Tambahkan di Vercel Environment Variables."
        )

    payload = json.dumps({
        "model":       model,
        "messages":    [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens":  1024,
        "temperature": 0.7,
    }).encode("utf-8")

    req = urllib.request.Request(
        url     = "https://api.groq.com/openai/v1/chat/completions",
        data    = payload,
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "User-Agent":    "TKA-Made-Easy/1.0",
        },
        method  = "POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise HTTPException(status_code=500, detail=f"Groq HTTP {e.code}: {error_body}")

# ---------------------------------------------------------------------------
# Gemini Helper  (digunakan khusus oleh endpoint /materi)
# ---------------------------------------------------------------------------

def call_gemini(prompt: str, model: str = "gemini-2.0-flash") -> str:
    """Kirim prompt ke Gemini API menggunakan google-generativeai SDK."""
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY tidak ditemukan. Tambahkan di file .env atau Vercel Environment Variables."
        )
    try:
        gemini_model = genai.GenerativeModel(model)
        response     = gemini_model.generate_content(prompt)
        return response.text
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gemini API error: {exc}")


# ---------------------------------------------------------------------------
# Inisialisasi Aplikasi FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TKA Made Easy — UTBK Tryout API",
    description="Backend API untuk menyimpan/mengambil skor dan soal tryout UTBK",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pastikan CORS header tetap ada meskipun backend crash 500
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
        headers={"Access-Control-Allow-Origin": "*"},
    )

# ---------------------------------------------------------------------------
# Database Helpers
# ---------------------------------------------------------------------------

def get_connection():
    """Membuka koneksi ke Supabase PostgreSQL menggunakan pg8000 (pure Python)."""
    url = urlparse(DATABASE_URL)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE
    conn = pg8000.connect(
        host     = url.hostname,
        port     = url.port or 5432,
        database = url.path.lstrip("/"),
        user     = url.username,
        password = url.password,
        ssl_context = ssl_ctx,
    )
    return conn


def rows_to_dicts(cursor) -> list[dict]:
    """Konversi hasil query ke list of dict (pengganti RealDictCursor)."""
    if cursor.description is None:
        return []
    cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def row_to_dict(cursor) -> dict | None:
    """Konversi satu baris hasil query ke dict."""
    row = cursor.fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in cursor.description]
    return dict(zip(cols, row))


def init_db() -> None:
    """Membuat tabel User_Score di Supabase jika belum ada."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS "User_Score" (
                id          SERIAL PRIMARY KEY,
                nama_user   TEXT   NOT NULL,
                mata_pel    TEXT   NOT NULL,
                skor        FLOAT  NOT NULL CHECK(skor >= 0 AND skor <= 100),
                tanggal     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        print("[DB] Koneksi ke Supabase berhasil ✅")
    except Exception as e:
        print(f"[DB] Gagal inisialisasi: {e}")
    finally:
        conn.close()


init_db()

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

MATA_PELAJARAN_VALID = [
    "Bahasa Jepang",
    "Bahasa Jerman",
    "Bahasa Indonesia",
    "Bahasa Inggris",
    "Ekonomi",
    "Fisika",
    "Geografi",
    "Informatika",
    "Indonesia Tingkat Lanjut",
    "Bahasa Inggris Tingkat Lanjut",
    "Kimia",
    "PKWU",
    "Matematika TL",
    "Matematika",
    "PKN",
    "Sejarah",
    "Sosiologi",
]


class SkorRequest(BaseModel):
    nama_user: str   = Field(..., min_length=2, max_length=100, examples=["Budi Santoso"])
    mata_pel:  str   = Field(..., examples=["Matematika"])
    skor:      float = Field(..., ge=0, le=100, examples=[87.5])


class SkorResponse(BaseModel):
    id:        int
    nama_user: str
    mata_pel:  str
    skor:      float
    tanggal:   str


class PostResponse(BaseModel):
    pesan: str
    data:  SkorResponse


# Models untuk endpoint AI
class MateriRequest(BaseModel):
    mata_pelajaran: str = Field(..., examples=["Fisika"])
    gaya_belajar:   str = Field(..., examples=["visual"])  # visual / auditori / kinestetik


class VariasiSoalRequest(BaseModel):
    soal_asli: str = Field(..., examples=["Diketahui f(x) = 3x² + 2x - 5. Tentukan f'(2)!"])


# ---------------------------------------------------------------------------
# Routes — Info
# ---------------------------------------------------------------------------

@app.get("/", tags=["Info"])
def root():
    return {
        "nama_aplikasi": "TKA Made Easy",
        "database":      "Supabase PostgreSQL (pg8000)",
        "status":        "Server berjalan ✅",
        "docs":          "/docs",
    }


# ---------------------------------------------------------------------------
# Routes — Scores
# ---------------------------------------------------------------------------

@app.post("/scores", response_model=PostResponse, status_code=201, tags=["Scores"])
def tambah_skor(payload: SkorRequest):
    """Kirim skor baru ke database."""
    if payload.mata_pel not in MATA_PELAJARAN_VALID:
        raise HTTPException(
            status_code=422,
            detail=f"Mata pelajaran '{payload.mata_pel}' tidak dikenal. Pilihan: {MATA_PELAJARAN_VALID}",
        )
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO "User_Score" (nama_user, mata_pel, skor)
            VALUES (%s, %s, %s)
            RETURNING id, nama_user, mata_pel, skor, tanggal::TEXT
            """,
            (payload.nama_user.strip(), payload.mata_pel, payload.skor),
        )
        row = row_to_dict(cur)
        conn.commit()
        cur.close()
        return PostResponse(pesan="Skor berhasil disimpan! 🎉", data=SkorResponse(**row))
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan skor: {exc}")
    finally:
        conn.close()


@app.get("/scores", response_model=list[SkorResponse], tags=["Scores"])
def ambil_semua_skor(
    nama_user: Optional[str] = None,
    mata_pel:  Optional[str] = None,
):
    """Ambil semua skor. Filter: `?nama_user=budi` atau `?mata_pel=Fisika`"""
    conn = get_connection()
    try:
        cur    = conn.cursor()
        query  = 'SELECT id, nama_user, mata_pel, skor, tanggal::TEXT FROM "User_Score" WHERE 1=1'
        params = []
        if nama_user:
            query += " AND LOWER(nama_user) LIKE %s"
            params.append(f"%{nama_user.lower()}%")
        if mata_pel:
            query += " AND mata_pel = %s"
            params.append(mata_pel)
        query += " ORDER BY tanggal DESC"
        cur.execute(query, params)
        rows = rows_to_dicts(cur)
        cur.close()
        return [SkorResponse(**r) for r in rows]
    finally:
        conn.close()


@app.get("/scores/{score_id}", response_model=SkorResponse, tags=["Scores"])
def ambil_skor_by_id(score_id: int):
    """Ambil satu skor berdasarkan ID."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, nama_user, mata_pel, skor, tanggal::TEXT FROM "User_Score" WHERE id = %s',
            (score_id,),
        )
        row = row_to_dict(cur)
        cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Skor ID {score_id} tidak ditemukan.")
        return SkorResponse(**row)
    finally:
        conn.close()


@app.delete("/scores/{score_id}", tags=["Scores"])
def hapus_skor(score_id: int):
    """Hapus satu skor berdasarkan ID."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM "User_Score" WHERE id = %s', (score_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Skor ID {score_id} tidak ditemukan.")
        cur.execute('DELETE FROM "User_Score" WHERE id = %s', (score_id,))
        conn.commit()
        cur.close()
        return {"pesan": f"Skor ID {score_id} berhasil dihapus."}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menghapus skor: {exc}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes — Soal (tabel "MAPEL MATA PELIA", topic = 'soal')
# ---------------------------------------------------------------------------

@app.get("/soal", tags=["Soal"])
def ambil_soal(
    mata_pelajaran: Optional[str] = None,
):
    """
    Ambil soal dari tabel "MAPEL MATA PELIA".
    - Hanya mengambil baris dengan `topic = 'soal'`
    - Mengembalikan kolom `content` apa adanya ke frontend
    - `?mata_pelajaran=Fisika` → soal Fisika saja
    """
    conn = get_connection()
    try:
        cur   = conn.cursor()
        query = (
            'SELECT mata_pelajaran, topic, content '
            'FROM "MAPEL MATA PELIA" WHERE topic = \'soal\''
        )
        params = []
        if mata_pelajaran:
            query += " AND mata_pelajaran = %s"
            params.append(mata_pelajaran)
        query += " ORDER BY mata_pelajaran"
        cur.execute(query, params)
        rows = rows_to_dicts(cur)
        cur.close()

        return {"total": len(rows), "soal": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil soal: {exc}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes — Materi (tabel "MAPEL MATA PELIA", topic != 'soal')
# ---------------------------------------------------------------------------

@app.get("/materi", tags=["Materi"])
def get_materi(mata_pelajaran: str):
    """
    Ambil materi dari tabel "MAPEL MATA PELIA".
    - Hanya mengambil baris yang `mata_pelajaran`-nya cocok dan `topic != 'soal'`
    - Setiap baris dianggap satu bab; kolom `topic` adalah nama bab, `content` adalah isinya
    - Seluruh konten digabung lalu dikirim ke Groq untuk diformat jadi HTML estetik
    """
    if mata_pelajaran not in MATA_PELAJARAN_VALID:
        raise HTTPException(
            status_code=422,
            detail=f"Mata pelajaran tidak valid: '{mata_pelajaran}'. Pilihan: {MATA_PELAJARAN_VALID}"
        )

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT topic, content FROM "MAPEL MATA PELIA" '
            "WHERE mata_pelajaran = %s AND topic != 'soal' "
            "ORDER BY topic",
            (mata_pelajaran,),
        )
        rows = rows_to_dicts(cur)
        cur.close()

        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"Materi untuk '{mata_pelajaran}' belum tersedia di database."
            )

        # Gabung semua bab menjadi satu teks mentah
        raw_text = "\n\n".join(
            f"### {r.get('topic', 'Materi')}\n{r.get('content', '')}"
            for r in rows
        )

        prompt = (
            "Kamu adalah desainer konten pendidikan untuk siswa SMA Indonesia. "
            "Tugasmu adalah mengubah materi pelajaran mentah menjadi HTML yang estetik, rapi, dan mudah dicerna. "
            "Gunakan tag HTML: <h2>, <h3>, <p>, <ul>, <li>, <strong>, <em>, <blockquote>. "
            "Tambahkan inline style CSS untuk warna (teal/biru muda/krem), padding, border-radius, dan spacing agar menarik secara visual. "
            "Bahasa: santai tapi akurat, seperti guru muda yang asik. "
            "WAJIB: Kembalikan HANYA kode HTML murni — tanpa markdown, tanpa ```html, langsung mulai dari tag HTML pertama.\n\n"
            f"Format ulang materi {mata_pelajaran} berikut menjadi HTML estetik:\n\n{raw_text}"
        )

        html_hasil = call_gemini(prompt)
        return {"mata_pelajaran": mata_pelajaran, "html": html_hasil}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal memproses materi: {exc}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes — AI (Groq)
# ---------------------------------------------------------------------------

@app.post("/ai/materi", tags=["AI"])
def ai_materi(payload: MateriRequest):
    """
    Jelaskan materi UTBK disesuaikan dengan gaya belajar user.

    - `mata_pelajaran`: Fisika, Kimia, Matematika, dll.
    - `gaya_belajar`  : `visual`, `auditori`, atau `kinestetik`
    """
    GAYA_VALID = ["visual", "auditori", "kinestetik"]
    if payload.gaya_belajar.lower() not in GAYA_VALID:
        raise HTTPException(
            status_code=422,
            detail=f"Gaya belajar tidak dikenal. Pilihan: {GAYA_VALID}"
        )

    system_prompt = (
        "Kamu adalah guru TKA UTBK yang sabar dan asyik buat anak SMA. "
        "Jelaskan materi dengan bahasa yang santai, mudah dipahami, dan sesuai gaya belajar yang diminta. "
        "Gunakan struktur yang jelas: pengertian → konsep utama → contoh soal singkat. "
        "Tidak perlu terlalu panjang, cukup padat dan mengena."
    )

    user_prompt = (
        f"Jelaskan materi {payload.mata_pelajaran} untuk persiapan UTBK TKA. "
        f"Gaya belajar saya adalah {payload.gaya_belajar}. "
        f"Sesuaikan cara penjelasannya:\n"
        f"- Visual: gunakan analogi, diagram teks (ASCII), dan poin-poin terstruktur.\n"
        f"- Auditori: gunakan penjelasan naratif mengalir seperti guru sedang bercerita.\n"
        f"- Kinestetik: gunakan langkah-langkah praktikal dan contoh nyata sehari-hari."
    )

    hasil = call_groq(system_prompt, user_prompt)
    return {
        "mata_pelajaran": payload.mata_pelajaran,
        "gaya_belajar":   payload.gaya_belajar,
        "materi":         hasil,
    }


@app.post("/ai/variasi-soal", tags=["AI"])
def ai_variasi_soal(payload: VariasiSoalRequest):
    """
    Buat 1 variasi soal baru dari soal asli dengan konteks/angka berbeda,
    tapi tingkat kesulitan dan konsep yang diuji tetap sama persis.
    """
    system_prompt = (
        "Kamu adalah pembuat soal UTBK TKA yang berpengalaman. "
        "Tugasmu adalah memodifikasi soal yang diberikan menjadi soal baru yang segar. "
        "Aturan ketat:\n"
        "1. Konsep dan rumus yang diuji HARUS sama persis.\n"
        "2. Ganti angka, nama, atau konteks cerita agar terasa berbeda.\n"
        "3. Tingkat kesulitan harus setara.\n"
        "4. Sertakan juga kunci jawaban dan langkah penyelesaiannya.\n"
        "Format output:\n"
        "SOAL BARU:\n[isi soal]\n\nPILIHAN JAWABAN:\nA. ...\nB. ...\nC. ...\nD. ...\nE. ...\n\n"
        "KUNCI: [huruf jawaban]\n\nPEMBAHASAN:\n[langkah penyelesaian]"
    )

    user_prompt = f"Buat variasi dari soal berikut:\n\n{payload.soal_asli}"

    hasil = call_groq(system_prompt, user_prompt)
    return {
        "soal_asli":   payload.soal_asli,
        "variasi_soal": hasil,
    }


@app.post("/ai/rekomendasi", tags=["AI"])
def ai_rekomendasi(nilai: dict):
    """
    Terima daftar mata pelajaran beserta nilainya, lalu kembalikan evaluasi
    dan rekomendasi strategi belajar dari 'guru BK' AI.

    Contoh body request:
    ```json
    { "Matematika": 40, "Sosiologi": 90, "Fisika": 55, "Kimia": 70 }
    ```
    """
    if not nilai:
        raise HTTPException(status_code=422, detail="Data nilai tidak boleh kosong.")

    # Susun ringkasan nilai untuk prompt
    ringkasan = "\n".join(
        f"- {mapel}: {skor}/100" for mapel, skor in nilai.items()
    )

    # Kategorikan mapel berdasarkan skor
    lemah   = [m for m, s in nilai.items() if s < 60]
    sedang  = [m for m, s in nilai.items() if 60 <= s < 80]
    kuat    = [m for m, s in nilai.items() if s >= 80]

    system_prompt = (
        "Kamu adalah guru BK sekaligus konselor belajar yang asik, jujur, dan supportif. "
        "Gaya bicaramu santai tapi tetap profesional — seperti kakak kelas yang udah berpengalaman UTBK. "
        "Berikan evaluasi yang jujur (tanpa menghakimi) dan rekomendasi strategi belajar yang konkret dan actionable. "
        "Jangan hanya teori — kasih contoh teknik belajar spesifik untuk setiap mata pelajaran yang lemah."
    )

    user_prompt = (
        f"Ini adalah hasil tryout UTBK TKA saya:\n{ringkasan}\n\n"
        f"Mata pelajaran yang lemah (< 60): {', '.join(lemah) if lemah else 'tidak ada'}.\n"
        f"Mata pelajaran yang sedang (60–79): {', '.join(sedang) if sedang else 'tidak ada'}.\n"
        f"Mata pelajaran yang kuat (≥ 80): {', '.join(kuat) if kuat else 'tidak ada'}.\n\n"
        f"Tolong berikan:\n"
        f"1. Evaluasi singkat kenapa nilai tertentu bisa anjlok.\n"
        f"2. Strategi belajar alternatif yang spesifik untuk tiap mapel lemah.\n"
        f"3. Prioritas belajar minggu ini berdasarkan data ini.\n"
        f"4. Motivasi penutup yang tulus dan tidak lebay."
    )

    hasil = call_groq(system_prompt, user_prompt)
    return {
        "ringkasan_nilai": nilai,
        "rekomendasi":     hasil,
    }


class ChatRequest(BaseModel):
    pesan: str = Field(..., min_length=1, examples=["Jelaskan hukum Newton"])

@app.get("/ai/test", tags=["AI"])
def ai_test():
    """Endpoint diagnosa — cek GROQ_API_KEY dan koneksi Groq."""
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return {"status": "❌ GROQ_API_KEY tidak ditemukan di environment variables."}
    key_preview = f"{key[:8]}...{key[-4:]}"
    try:
        hasil = call_groq("Jawab hanya dengan kata OK.", "test", model="llama-3.1-8b-instant")
        return {"status": "✅ Groq terhubung", "key_terbaca": key_preview, "response": hasil}
    except HTTPException as e:
        return {"status": "❌ Groq error", "key_terbaca": key_preview, "detail": e.detail}
    except Exception as e:
        return {"status": "❌ Unknown error", "key_terbaca": key_preview, "detail": str(e)}


@app.post("/ai/chat", tags=["AI"])
def ai_chat(payload: ChatRequest):
    """
    General chat endpoint untuk AI Assistant di frontend.
    Body: { "pesan": "pertanyaan user" }
    """
    system_prompt = (
        "Kamu adalah TKA AI Assistant — asisten belajar UTBK yang pintar, asik, dan supportif. "
        "Kamu membantu siswa SMA mempersiapkan ujian UTBK TKA (Tes Kemampuan Akademik) "
        "yang mencakup Matematika, Fisika, Kimia, Biologi, Ekonomi, Sosiologi, Geografi, Sejarah, "
        "Bahasa Indonesia, Bahasa Inggris, dan mata pelajaran TKA lainnya. "
        "Gaya bicaramu santai, friendly, dan pakai bahasa anak muda Indonesia — tapi tetap akurat. "
        "Kalau ada soal, jelaskan step-by-step. Kalau ada pertanyaan konsep, beri analogi yang mudah dipahami. "
        "Jawab singkat dan padat kecuali diminta penjelasan panjang."
    )
    hasil = call_groq(system_prompt, payload.pesan, model="llama-3.1-8b-instant")
    return {"balasan": hasil}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
