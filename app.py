from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from injector import InjectError, inject_bacon_core

BASE = Path(__file__).resolve().parent
PLUGIN = BASE / "plugins" / "BaconClickerCore.dylib"
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_BYTES", str(4 * 1024 * 1024 * 1024)))  # 4 GiB

app = FastAPI(title="Bacon IPA Injector", version="1.0.0")


def safe_stem(name: str) -> str:
    stem = Path(name or "App.ipa").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem[:100] or "App"


def cleanup_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


@app.get("/api/health")
def health():
    return {"ok": True, "plugin": PLUGIN.is_file(), "version": "1.0.0"}


@app.post("/api/inject")
async def inject(background_tasks: BackgroundTasks, ipa: UploadFile = File(...)):
    if not ipa.filename or not ipa.filename.lower().endswith(".ipa"):
        raise HTTPException(400, "請上傳 .ipa 檔案。")
    if not PLUGIN.is_file():
        raise HTTPException(500, "伺服器缺少 BaconClickerCore.dylib。")

    work = Path(tempfile.mkdtemp(prefix="baconweb_"))
    source = work / "source.ipa"
    output_name = f"{safe_stem(ipa.filename)}-BaconInjected-Unsigned.ipa"
    output = work / output_name

    total = 0
    try:
        with source.open("wb") as f:
            while True:
                chunk = await ipa.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, "IPA 太大，超過伺服器允許的上限。")
                f.write(chunk)

        try:
            result = inject_bacon_core(source, PLUGIN, output)
        except InjectError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:
            raise HTTPException(500, f"處理失敗：{type(e).__name__}: {e}") from e

        background_tasks.add_task(cleanup_dir, str(work))
        headers = {
            "X-Bacon-App": result.app_name.encode("utf-8", "ignore").decode("latin-1", "ignore"),
            "X-Bacon-Bundle": result.bundle_id,
            "X-Bacon-Injected": "1" if result.injected else "0",
        }
        return FileResponse(
            path=output,
            media_type="application/octet-stream",
            filename=output_name,
            headers=headers,
            background=background_tasks,
        )
    except HTTPException:
        cleanup_dir(str(work))
        raise
    except Exception:
        cleanup_dir(str(work))
        raise
    finally:
        await ipa.close()



@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE / "index.html", media_type="text/html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
