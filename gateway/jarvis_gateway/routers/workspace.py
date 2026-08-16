"""Endpoints del explorador de archivos del HUD.

Todo pasa por `WorkspaceGuard`: resuelve las rutas contra la raíz del workspace
y rechaza lo que se salga —rutas absolutas, `..` y enlaces que apunten fuera—.
Sin eso, un explorador servido por el gateway sería un lector de todo el disco
con la llave del gateway como única puerta.

`/workspace/file/raw` es el único que autentica por `?token=`: el navegador no
puede poner cabeceras en `<img src>` ni al abrir una vista previa. Por eso sale
con `nosniff`, CSP de aislamiento y, salvo imágenes, como descarga: un HTML del
workspace servido en el origen del HUD podría leer su almacenamiento.

Los servicios se leen del módulo `runtime` y no se importan por nombre: un
`from ... import settings` congela la referencia en el import y sustituirla
después, como hacen los tests, no tendría efecto aquí.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from jarvis_core.tools.builtin.filesystem import WorkspaceGuard
from pydantic import BaseModel

from jarvis_gateway import runtime
from jarvis_gateway.runtime import require_api_key

logger = logging.getLogger("jarvis.gateway.workspace")
router = APIRouter(tags=["workspace"])

#: Lo único que el HUD necesita mostrar dentro de la página. Todo lo demás baja
#: como descarga: un `.html` del workspace servido con su propio tipo se
#: ejecutaría en el origen del HUD y podría leer su `localStorage`, que es donde
#: vive la llave del gateway. Basta con que JARVIS escriba ahí un fichero —o que
#: alguien lo deje en la carpeta montada— para que abrirlo entregue la llave.
INLINE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif",
})


def _safe_delivery_headers(mime: str | None, filename: str) -> dict[str, str]:
    headers = {
        # Sin esto, el navegador puede ignorar el tipo declarado y adivinar por
        # el contenido, que es justo la vuelta al problema.
        "X-Content-Type-Options": "nosniff",
        # Aunque algo llegase a interpretarse, sin origen ni scripts no puede
        # tocar el almacenamiento del HUD.
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if mime not in INLINE_TYPES:
        # Las comillas del nombre romperían la cabecera; se quedan fuera.
        limpio = filename.replace('"', "").replace("\\", "").replace("\n", "")
        headers["Content-Disposition"] = f'attachment; filename="{limpio}"'
    return headers


class WorkspaceCreateRequest(BaseModel):
    path: str
    is_dir: bool = False
    content: str | None = None


def _get_workspace_guard() -> WorkspaceGuard:
    # El ajuste se llama workspace_max_file_bytes. Con el nombre corto esto
    # lanzaba AttributeError en cada llamada y el `except` de más abajo lo
    # convertía en una carpeta vacía: el explorador no mostraba nada nunca y no
    # había forma de saber por qué.
    return WorkspaceGuard(runtime.settings.workspace_root, runtime.settings.workspace_max_file_bytes)


@router.get("/workspace/tree", dependencies=[Depends(require_api_key)])
async def workspace_tree(path: str = "."):
    try:
        guard = _get_workspace_guard()
        raw = (path or ".").strip()
        
        resolved = None
        try:
            candidate = guard.resolve(raw, allow_root=True)
            if candidate.exists() and candidate.is_dir():
                resolved = candidate
        except Exception:
            pass

        if resolved is None:
            resolved = guard.resolve(".", allow_root=True)

        items = []
        try:
            raw_entries = list(resolved.iterdir())
        except Exception as exc:
            logger.warning("Error leyendo directorio %s: %s", resolved, exc)
            disp_p = "."
            try:
                disp_p = guard.display(resolved)
            except Exception:
                pass
            return {"path": disp_p, "items": []}

        def safe_sort_key(item: Path) -> tuple[bool, str]:
            is_directory = False
            try:
                is_directory = item.is_dir()
            except Exception:
                pass
            return (not is_directory, item.name.lower())

        entries = sorted(raw_entries, key=safe_sort_key)

        for entry in entries:
            try:
                is_dir = False
                is_symlink = False
                size = 0
                mod_time = 0
                
                try:
                    is_symlink = entry.is_symlink()
                except Exception:
                    pass

                try:
                    is_dir = entry.is_dir()
                except Exception:
                    pass

                if not is_dir and not is_symlink:
                    try:
                        if entry.is_file():
                            size = entry.stat().st_size
                    except Exception:
                        pass

                try:
                    mod_time = entry.stat().st_mtime
                except Exception:
                    pass

                try:
                    rel = guard.display(entry)
                except Exception:
                    rel = entry.name

                ext = entry.suffix.lower() if not is_dir else ""
                mime, _ = mimetypes.guess_type(entry.name)

                items.append({
                    "name": entry.name,
                    "path": rel,
                    "is_dir": is_dir,
                    "is_symlink": is_symlink,
                    "size": size,
                    "extension": ext,
                    "mime": mime or ("directory" if is_dir else "application/octet-stream"),
                    "mod_time": mod_time,
                })
            except Exception as item_err:
                logger.warning("Error leyendo item %s en workspace: %s", entry.name, item_err)
                continue

        try:
            disp_path = guard.display(resolved)
        except Exception:
            disp_path = "."

        parent = None
        if disp_path != ".":
            grandparent = str(Path(disp_path).parent)
            parent = "." if grandparent == "." else grandparent

        return {"path": disp_path, "parent": parent, "items": items}
    except HTTPException:
        raise
    except Exception as top_err:
        # Antes esto devolvía {"items": []} y el explorador salía vacío sin decir
        # nada: un fallo de configuración era indistinguible de una carpeta sin
        # archivos. La tolerancia por entrada de arriba sí tiene sentido —un
        # archivo ilegible no debe tumbar el listado—, pero tragarse el fallo
        # entero solo esconde la causa.
        logger.exception("No pude listar el workspace")
        raise HTTPException(
            status_code=500,
            detail=f"No pude listar la carpeta: {type(top_err).__name__}.",
        ) from top_err


@router.get("/workspace/file/content", dependencies=[Depends(require_api_key)])
async def workspace_file_content(path: str):
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="El archivo no existe.")
    size = resolved.stat().st_size
    if size > runtime.settings.workspace_max_file_bytes:
        raise HTTPException(status_code=400, detail="El archivo supera el tamaño máximo permitido.")
    
    mime, _ = mimetypes.guess_type(resolved.name)
    ext = resolved.suffix.lower()
    
    is_binary_ext = ext in {".docx", ".doc", ".pdf", ".zip", ".tar", ".gz", ".7z", ".xlsx", ".pptx", ".exe", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".mp3", ".wav"}
    
    if is_binary_ext:
        return {
            "path": guard.display(resolved),
            "name": resolved.name,
            "content": f"[Documento/Archivo {resolved.name} ({size} bytes)]",
            "size": size,
            "extension": ext,
            "mime": mime or "application/octet-stream",
            "is_binary": True,
        }

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        return {
            "path": guard.display(resolved),
            "name": resolved.name,
            "content": content,
            "size": size,
            "extension": ext,
            "mime": mime or "text/plain",
            "is_binary": False,
        }
    except Exception as exc:
        return {
            "path": guard.display(resolved),
            "name": resolved.name,
            "content": f"[Error leyendo archivo: {exc}]",
            "size": size,
            "extension": ext,
            "mime": mime or "application/octet-stream",
            "is_binary": True,
        }


@router.get("/workspace/file/raw")
async def workspace_file_raw(path: str = "", token: str = ""):
    # `path` sin valor por defecto haría que FastAPI validara los parámetros
    # antes de llegar aquí, y quien no tiene la llave recibiría un 422 que ya le
    # cuenta el contrato del endpoint. La llave se comprueba primero.
    if not runtime.valid_api_key(token):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="El archivo no existe.")
    size = resolved.stat().st_size
    if size > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archivo multimedia demasiado grande.")
    mime, _ = mimetypes.guess_type(resolved.name)
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(
        content=data,
        media_type=mime if mime in INLINE_TYPES else "application/octet-stream",
        headers=_safe_delivery_headers(mime, resolved.name),
    )


@router.post("/workspace/file/create", dependencies=[Depends(require_api_key)])
async def workspace_file_create(req: WorkspaceCreateRequest):
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.is_dir:
        resolved.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "message": f"Carpeta creada: {guard.display(resolved)}"}
    else:
        if resolved.exists():
            raise HTTPException(status_code=400, detail="El archivo ya existe.")
        content = req.content or ""
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {"status": "ok", "message": f"Archivo creado: {guard.display(resolved)}"}


@router.delete("/workspace/file/delete", dependencies=[Depends(require_api_key)])
async def workspace_file_delete(path: str):
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="La ruta no existe.")
    try:
        if resolved.is_dir():
            resolved.rmdir()
        else:
            resolved.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo eliminar: {exc}")
    return {"status": "ok", "message": f"Eliminado: {guard.display(resolved)}"}

