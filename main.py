"""
=============================================================
  TKA Made Easy — UTBK Tryout Backend
  Framework : FastAPI
  Database  : SQLite (via sqlite3 bawaan Python)
=============================================================
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------

DB_PATH = "tka_made_easy.db"   # File database SQLite (dibuat otomatis)

# ---------------------------------------------------------------------------
# Inisialisasi Aplikasi FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TKA Made Easy — UTBK Tryout API",
    description="Backend API untuk menyimpan dan mengambil skor tryout UTBK",
    version="1.0.0",
)

# CORS — izinkan semua origin agar frontend bisa connect (sesuaikan di production)
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

def get_connection() -> sqlite3.Connection:
    """Membuka koneksi ke database SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # hasil query bisa diakses seperti dict
    return conn


def init_db() -> None:
    """
    Membuat tabel 'User_Score' jika belum ada.
    Dipanggil sekali saat server pertama kali menyala.
    """
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS User_Score (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nama_user   TEXT    NOT NULL,
                mata_pel    TEXT    NOT NULL,
                skor        REAL    NOT NULL CHECK(skor >= 0 AND skor <= 100),
                tanggal     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.commit()
        print(f"[DB] Database siap → {os.path.abspath(DB_PATH)}")
    finally:
        conn.close()


# Inisialisasi database saat aplikasi pertama kali dimuat
init_db()

# ---------------------------------------------------------------------------
# Pydantic Models (validasi data request & response)
# ---------------------------------------------------------------------------

MATA_PELAJARAN_VALID = [
    "Matematika",
    "Fisika",
    "Kimia",
    "Biologi",
    "Ekonomi",
    "Sosiologi",
    "Geografi",
    "Sejarah",
]


class SkorRequest(BaseModel):
    """Body request untuk mengirim skor baru."""
    nama_user: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Nama lengkap peserta tryout",
        examples=["Budi Santoso"],
    )
    mata_pel: str = Field(
        ...,
        description="Mata pelajaran yang diujikan",
        examples=["Matematika"],
    )
    skor: float = Field(
        ...,
        ge=0,
        le=100,
        description="Nilai tryout (0 – 100)",
        examples=[87.5],
    )


class SkorResponse(BaseModel):
    """Struktur data satu baris skor untuk response GET."""
    id: int
    nama_user: str
    mata_pel: str
    skor: float
    tanggal: str


class PostResponse(BaseModel):
    """Response setelah skor berhasil disimpan."""
    pesan: str
    data: SkorResponse


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", tags=["Info"])
def root():
    """
    Endpoint dasar — cek apakah server berjalan.
    """
    return {
        "nama_aplikasi": "TKA Made Easy",
        "status": "Server berjalan ✅",
        "docs": "/docs",
    }


# ── POST /scores ──────────────────────────────────────────────────────────

@app.post("/scores", response_model=PostResponse, status_code=201, tags=["Scores"])
def tambah_skor(payload: SkorRequest):
    """
    **Kirim skor baru ke database.**

    - `nama_user`: nama peserta (min 2 karakter)
    - `mata_pel` : nama mata pelajaran
    - `skor`     : nilai antara 0 – 100
    """
    # Validasi mata pelajaran (opsional — hapus blok ini jika ingin bebas)
    if payload.mata_pel not in MATA_PELAJARAN_VALID:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Mata pelajaran '{payload.mata_pel}' tidak dikenal. "
                f"Pilihan yang tersedia: {MATA_PELAJARAN_VALID}"
            ),
        )

    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO User_Score (nama_user, mata_pel, skor)
            VALUES (?, ?, ?)
            """,
            (payload.nama_user.strip(), payload.mata_pel, payload.skor),
        )
        conn.commit()
        new_id = cursor.lastrowid

        # Ambil baris yang baru saja disimpan untuk dikembalikan ke client
        row = conn.execute(
            "SELECT * FROM User_Score WHERE id = ?", (new_id,)
        ).fetchone()

        return PostResponse(
            pesan="Skor berhasil disimpan! 🎉",
            data=SkorResponse(**dict(row)),
        )
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan skor: {exc}")
    finally:
        conn.close()


# ── GET /scores ───────────────────────────────────────────────────────────

@app.get("/scores", response_model=list[SkorResponse], tags=["Scores"])
def ambil_semua_skor(
    nama_user: Optional[str] = None,
    mata_pel:  Optional[str] = None,
):
    """
    **Ambil semua skor dari database.**

    Query params opsional untuk filter:
    - `nama_user`: filter berdasarkan nama (pencarian parsial, tidak case-sensitive)
    - `mata_pel` : filter berdasarkan mata pelajaran
    
    Contoh:
    - `/scores` → semua skor
    - `/scores?nama_user=budi` → skor milik user bernama "budi" (atau mengandung kata itu)
    - `/scores?mata_pel=Matematika` → semua skor Matematika
    """
    conn = get_connection()
    try:
        query  = "SELECT * FROM User_Score WHERE 1=1"
        params = []

        if nama_user:
            query  += " AND LOWER(nama_user) LIKE ?"
            params.append(f"%{nama_user.lower()}%")

        if mata_pel:
            query  += " AND mata_pel = ?"
            params.append(mata_pel)

        query += " ORDER BY tanggal DESC"

        rows = conn.execute(query, params).fetchall()
        return [SkorResponse(**dict(r)) for r in rows]
    finally:
        conn.close()


# ── GET /scores/{id} ──────────────────────────────────────────────────────

@app.get("/scores/{score_id}", response_model=SkorResponse, tags=["Scores"])
def ambil_skor_by_id(score_id: int):
    """
    **Ambil satu skor berdasarkan ID.**
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM User_Score WHERE id = ?", (score_id,)
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Skor dengan ID {score_id} tidak ditemukan."
            )
        return SkorResponse(**dict(row))
    finally:
        conn.close()


# ── DELETE /scores/{id} ───────────────────────────────────────────────────

@app.delete("/scores/{score_id}", tags=["Scores"])
def hapus_skor(score_id: int):
    """
    **Hapus satu skor berdasarkan ID.**
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM User_Score WHERE id = ?", (score_id,)
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Skor dengan ID {score_id} tidak ditemukan."
            )

        conn.execute("DELETE FROM User_Score WHERE id = ?", (score_id,))
        conn.commit()
        return {"pesan": f"Skor ID {score_id} berhasil dihapus."}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menghapus skor: {exc}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point (jalankan langsung dengan `python main.py`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)