"""Qué le está permitido hacer a *esta* máquina.

La política vive en el nodo, no en el cerebro. Es la diferencia entre "JARVIS decide
qué puede hacer en tu PC" y "tu PC decide qué le deja hacer a JARVIS": si alguien
compromete el gateway —o el modelo se traga una inyección desde un correo—, el nodo
sigue negándose a lo que su dueño no declaró.

Por eso el manifiesto se publica **desde el nodo**: el gateway aprende qué puede pedir,
no lo elige. Un servidor de casa puede exponer `docker`; el portátil, no.

El modo de solo lectura es el que viene de fábrica. Escribir en disco o ejecutar
programas exige activarlo a mano en la configuración de la máquina, porque un fallo al
configurar debe dejarte con un nodo inútil, nunca con uno peligroso.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Programas que el nodo nunca ejecuta, esté como esté configurado.
#:
#: Son el equivalente en la máquina de `CRITICAL_DENY_RULES` del núcleo, y existen
#: aparte a propósito: la lista del cerebro protege de lo que el modelo pida, esta
#: protege de lo que *cualquiera* pida, incluido un gateway comprometido.
FORBIDDEN_EXECUTABLES = frozenset(
    {
        "mkfs", "mkfs.ext4", "mkfs.ntfs", "format",
        "dd",
        "shutdown", "reboot", "halt", "poweroff",
        "diskpart", "fdisk", "parted",
        "userdel", "passwd", "chpasswd",
    }
)

#: Rutas que nunca se leen ni se escriben, aunque caigan dentro de una raíz permitida.
#: Las claves SSH y los almacenes de credenciales no son "un archivo más": basta una
#: lectura para perderlas, y ninguna operación legítima de JARVIS las necesita.
FORBIDDEN_PATH_PARTS = ("/.ssh/", "/.aws/", "/.gnupg/", "/.env")


def _nombre_de_programa(ruta: str) -> str:
    """Reduce una ruta de ejecutable a su nombre comparable.

    Se compara por nombre base porque `/usr/bin/git` y `git` son el mismo programa, y
    permitir uno y no el otro solo invita a rodear la lista con una ruta absoluta.

    Los dos separadores se parten siempre, no los del sistema anfitrión:
    `os.path.basename` en Linux no rompe por `\\`, así que un
    `C:\\Windows\\System32\\dd.exe` llegado de un gateway se comparaba entero y no
    coincidía con `dd` — esquivando la lista de prohibidos justo en el caso que más
    importa. El `.exe` se recorta por lo mismo.
    """
    base = ruta.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base.removesuffix(".exe")


class CapabilityError(PermissionError):
    """El nodo se niega: la operación no está declarada para esta máquina."""


@dataclass
class NodeCapabilities:
    """El contrato que este nodo ofrece, y contra el que valida cada petición."""

    node_id: str
    #: Raíces dentro de las que se puede leer. Fuera de ellas no existe nada.
    read_roots: list[Path] = field(default_factory=list)
    #: Raíces dentro de las que se puede escribir. Siempre subconjunto efectivo de
    #: lo legible: escribir donde no se puede leer no tiene ningún caso de uso y sí
    #: complica razonar sobre el alcance.
    write_roots: list[Path] = field(default_factory=list)
    #: Programas ejecutables por nombre. Vacío = no se ejecuta nada.
    allowed_executables: list[str] = field(default_factory=list)
    #: Sin esto, toda operación que modifique algo se rechaza.
    allow_writes: bool = False
    allow_process_control: bool = False
    max_output_bytes: int = 100_000
    command_timeout: float = 30.0

    def manifest(self) -> dict[str, object]:
        """Lo que el nodo anuncia al gateway.

        Se publican las raíces y los ejecutables porque el gateway los necesita para
        no pedir imposibles, pero nunca es la fuente de verdad: el nodo revalida cada
        petición contra estos mismos datos.
        """
        return {
            "node_id": self.node_id,
            "operations": sorted(self.available_operations()),
            "read_roots": [str(p) for p in self.read_roots],
            "write_roots": [str(p) for p in self.write_roots],
            "executables": sorted(self.allowed_executables),
            "allow_writes": self.allow_writes,
            "allow_process_control": self.allow_process_control,
        }

    def available_operations(self) -> set[str]:
        ops = {"system_info", "list_directory", "read_file"}
        if self.allowed_executables:
            ops.add("run_command")
        if self.allow_writes and self.write_roots:
            ops.add("write_file")
        if self.allow_process_control:
            ops.update({"list_processes", "kill_process"})
        return ops

    # -- validación ------------------------------------------------------

    def check_operation(self, op: str) -> None:
        if op not in self.available_operations():
            raise CapabilityError(f"La operación '{op}' no está declarada en este nodo.")

    def resolve_path(self, raw: str, *, write: bool = False) -> Path:
        """Convierte una ruta pedida en una ruta real y comprobada.

        Se resuelven enlaces simbólicos **antes** de comparar: sin eso, un enlace
        dentro de una raíz permitida apuntando a `/etc` convertiría la confinación en
        decorativa. `Path.resolve()` normaliza además los `..`, así que no hace falta
        buscarlos a mano.
        """
        if not raw or not str(raw).strip():
            raise CapabilityError("Ruta vacía.")
        candidate = Path(raw).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise CapabilityError(f"Ruta ilegible: {exc}") from exc

        # La comparación es sobre texto con separadores normalizados para que
        # funcione igual en Windows y en POSIX.
        as_text = resolved.as_posix()
        for parte in FORBIDDEN_PATH_PARTS:
            if parte in as_text + "/":
                raise CapabilityError(f"Ruta protegida: contiene '{parte.strip('/')}'.")

        if write and not self.allow_writes:
            raise CapabilityError("Este nodo está en modo de solo lectura.")
        roots = self.write_roots if write else self.read_roots
        if not roots:
            raise CapabilityError(
                "No hay raíces de escritura declaradas."
                if write
                else "No hay raíces de lectura declaradas."
            )
        for root in roots:
            try:
                resolved.relative_to(root.expanduser().resolve())
            except ValueError:
                continue
            return resolved
        raise CapabilityError(f"'{resolved}' queda fuera de las raíces permitidas.")

    def check_executable(self, argv: list[str]) -> str:
        """Valida el programa de un `run_command` y devuelve su nombre."""
        if not argv:
            raise CapabilityError("argv vacío.")
        if not all(isinstance(a, str) for a in argv):
            raise CapabilityError("argv debe ser una lista de cadenas.")
        nombre = _nombre_de_programa(argv[0])
        if nombre in FORBIDDEN_EXECUTABLES:
            raise CapabilityError(f"'{nombre}' está prohibido en cualquier nodo.")
        permitidos = {_nombre_de_programa(e) for e in self.allowed_executables}
        if nombre not in permitidos:
            raise CapabilityError(f"'{nombre}' no está en los ejecutables de este nodo.")
        return nombre
