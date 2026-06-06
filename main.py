"""
=============================================================
  TKA Made Easy — UTBK Tryout Backend
  Framework : FastAPI
  Database  : PostgreSQL (Supabase) via pg8000 (pure Python)
=============================================================
"""

import os
import ssl
from urllib.parse import urlparse
from typing import Optional

import pg8000
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Load environment variables dari file .env
# ---------------------------------------------------------------------------

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL tidak ditemukan. Pastikan file .env sudah dibuat.")

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
    "Matematika", "Fisika", "Kimia", "Biologi",
    "Ekonomi", "Sosiologi", "Geografi", "Sejarah",
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
# Routes — Soal (tabel "MAPEL MATA PELIA")
# ---------------------------------------------------------------------------

@app.get("/soal", tags=["Soal"])
def ambil_soal(
    mata_pelajaran: Optional[str] = None,
    limit:          Optional[int] = None,
):
    """
    Ambil soal dari tabel MAPEL MATA PELIA.
    - `?mata_pelajaran=Biologi` → soal Biologi saja
    - `?limit=10`               → maksimal 10 soal
    """
    conn = get_connection()
    try:
        cur    = conn.cursor()
        query  = 'SELECT * FROM "MAPEL MATA PELIA" WHERE 1=1'
        params = []
        if mata_pelajaran:
            query += " AND mata_pelajaran = %s"
            params.append(mata_pelajaran)
        query += " ORDER BY id"
        if limit and limit > 0:
            query += " LIMIT %s"
            params.append(limit)
        cur.execute(query, params)
        rows = rows_to_dicts(cur)
        cur.close()
        return {"total": len(rows), "soal": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil soal: {exc}")
    finally:
        conn.close()


@app.get("/soal/{soal_id}", tags=["Soal"])
def ambil_soal_by_id(soal_id: int):
    """Ambil satu soal berdasarkan ID."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM "MAPEL MATA PELIA" WHERE id = %s', (soal_id,))
        row = row_to_dict(cur)
        cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Soal ID {soal_id} tidak ditemukan.")
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil soal: {exc}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
