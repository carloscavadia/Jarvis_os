"""El visor: abrir cosas para mirar sin que se pueda abrir cualquier cosa.

Un visor es una superficie de ataque tranquila hasta que deja de serlo: si
acepta una ruta que se sale del workspace, es un lector de todo el disco; si
acepta cualquier URL en un iframe, es una ventana con el origen del HUD detrás.
Aquí se fija lo que NO puede abrir.
"""

import asyncio

import pytest
from jarvis_core.tools.builtin.filesystem import WorkspaceGuard
from jarvis_core.tools.builtin.viewer import OpenViewerTool, youtube_video_id


@pytest.fixture()
def visor(tmp_path):
    raiz = tmp_path / "workspace"
    raiz.mkdir()
    (raiz / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (raiz / "informe.pdf").write_bytes(b"%PDF-1.4")
    (raiz / "notas.md").write_text("# Notas", encoding="utf-8")
    (raiz / "programa.exe").write_bytes(b"MZ")
    (tmp_path / "secreto.png").write_bytes(b"\x89PNG")
    return OpenViewerTool(WorkspaceGuard(str(raiz), 256 * 1024)), tmp_path


def abrir(tool, **kwargs):
    return asyncio.run(tool.run(**kwargs))


def test_una_imagen_del_workspace_se_abre(visor):
    tool, _ = visor
    resultado = abrir(tool, kind="image", path="foto.png", title="La foto")
    assert not resultado.is_error


def test_una_ruta_que_sale_del_workspace_se_rechaza(visor):
    tool, _ = visor
    resultado = abrir(tool, kind="image", path="../secreto.png")
    assert resultado.is_error


def test_una_ruta_absoluta_no_alcanza_el_disco(visor):
    tool, _ = visor
    resultado = abrir(tool, kind="document", path="/etc/passwd")
    assert resultado.is_error


def test_el_visor_de_imagen_no_abre_un_ejecutable(visor):
    """La extensión importa: un `.exe` no se enseña como si fuera una foto."""
    tool, _ = visor
    resultado = abrir(tool, kind="image", path="programa.exe")
    assert resultado.is_error
    assert ".png" in resultado.content


def test_un_archivo_que_no_existe_lo_dice_en_vez_de_abrir_una_ventana_vacia(visor):
    tool, _ = visor
    resultado = abrir(tool, kind="pdf", path="no-esta.pdf")
    assert resultado.is_error
    assert "no existe" in resultado.content.lower()


def test_un_pdf_y_un_documento_se_abren(visor):
    tool, _ = visor
    assert not abrir(tool, kind="pdf", path="informe.pdf").is_error
    assert not abrir(tool, kind="document", path="notas.md").is_error


def test_un_tipo_de_visor_inventado_se_rechaza(visor):
    tool, _ = visor
    assert abrir(tool, kind="hologram", path="foto.png").is_error


@pytest.mark.parametrize(
    "entrada",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=42",
        "dQw4w9WgXcQ",
    ],
)
def test_se_reconocen_las_formas_habituales_de_un_enlace_de_youtube(entrada):
    assert youtube_video_id(entrada) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "entrada",
    [
        "",
        "https://ejemplo.com/watch?v=dQw4w9WgXcQ",
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.atacante.net/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=corto",
        "javascript:alert(1)",
    ],
)
def test_lo_que_no_es_youtube_no_pasa_por_youtube(entrada):
    """Un dominio que solo *empieza* por youtube.com no es YouTube.

    Este es el caso que importa: `youtube.com.atacante.net` engaña a cualquier
    comprobación hecha con `startswith`, y lo que acabaría dentro del iframe
    sería una página de quien registrase ese dominio.
    """
    assert youtube_video_id(entrada) == ""


def test_un_video_que_no_es_de_youtube_no_abre_ventana(visor):
    tool, _ = visor
    assert abrir(tool, kind="video", url="https://vimeo.com/12345").is_error


def test_una_web_privada_no_se_abre(visor):
    """El visor web sale a la red: hereda la misma protección que fetch_web_page."""
    tool, _ = visor
    assert abrir(tool, kind="web", url="https://192.168.1.1/").is_error
    assert abrir(tool, kind="web", url="http://ejemplo.com/").is_error


# ── Corregir el visor cuando la URL dice otra cosa ────────────────────────────

def test_un_enlace_de_youtube_pedido_como_web_se_abre_como_video(visor):
    """El caso real: se pidió `kind='web'` para un vídeo de YouTube.

    Es una elección razonable —es una página— y el resultado era absurdo:
    intentar leerla como texto, tragarse un mega de HTML de la portada y no
    enseñar el vídeo. Un enlace a un vídeo es un vídeo, lo llame como lo llame
    quien lo pide.
    """
    from jarvis_core.tools.builtin.viewer import resolve_kind

    assert resolve_kind("web", "https://www.youtube.com/watch?v=yxW5yuzVi8w") == "video"
    assert resolve_kind("web", "https://youtu.be/yxW5yuzVi8w") == "video"

    tool, _ = visor
    resultado = abrir(tool, kind="web", url="https://www.youtube.com/watch?v=yxW5yuzVi8w")
    assert not resultado.is_error
    assert "Vídeo" in resultado.content


def test_una_web_que_no_es_youtube_sigue_siendo_web(visor):
    from jarvis_core.tools.builtin.viewer import resolve_kind

    assert resolve_kind("web", "https://es.wikipedia.org/wiki/Paella") == "web"
    # Y la corrección no toca los demás visores.
    assert resolve_kind("image", "https://www.youtube.com/watch?v=yxW5yuzVi8w") == "image"


def test_con_navegador_instalado_el_visor_web_manda_usar_browse(tmp_path):
    """Con navegador no hay razón para enseñar una página como texto.

    `browse` la muestra tal cual se ve. Devolverlo como error con la salida a
    mano hace que el modelo reintente por el camino bueno, en vez de dar por
    hecho que enseñó el sitio cuando solo enseñó su texto.
    """
    from jarvis_core.tools.builtin.viewer import OpenViewerTool

    raiz = tmp_path / "ws"
    raiz.mkdir()
    tool = OpenViewerTool(WorkspaceGuard(str(raiz), 1024), browser_available=True)
    salida = abrir(tool, kind="web", url="https://es.wikipedia.org/wiki/Paella")
    assert salida.is_error
    assert "browse" in salida.content


def test_sin_navegador_el_visor_web_sigue_dando_el_texto(visor):
    tool, _ = visor
    salida = abrir(tool, kind="web", url="https://es.wikipedia.org/wiki/Paella")
    assert not salida.is_error
    # Y avisa de que es texto, para que no se describa una página que no vio.
    assert "solo texto" in salida.content.lower()
