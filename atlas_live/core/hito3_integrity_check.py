"""HITO 4 -- Fase 4.2 (2026-09-04, autorizado explícitamente en Plan Mode):
autodiagnóstico de integridad de Hito 3, en código, repetible -- convierte
los checks estáticos que se hicieron A MANO (grep/AST) en cada auditoría
de esta sesión en funciones reales, testeadas, para que una auditoría
futura no dependa de que se repitan manualmente.

Puramente de LECTURA sobre el código fuente en disco -- `ast`/`inspect`,
nunca una DB, nunca la red, nunca importa nada de `atlas_decision_core.py`/
`activation_registry.py` más allá de para ubicar sus archivos. No puede,
por diseño, activar nada ni tocar ningún dato real."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# Este propio archivo NUNCA se audita a sí mismo -- necesita mencionar,
# como datos (nombres de función prohibidos, palabras de vocabulario
# financiero), exactamente lo que busca; excluirlo evita que el detector
# se autodetecte como una violación (confirmado como fallo real durante
# la implementación de Fase 4.2, 2026-09-04).
_SELF_FILE = Path(__file__).resolve()

_DEFAULT_SEARCH_ROOTS = [_REPO_ROOT / "atlas_live", _REPO_ROOT / "atlas"]

# Los archivos "core" de Hito 3 -- donde nunca debe aparecer vocabulario de
# ejecución financiera. Rutas relativas a `_REPO_ROOT`.
_HITO3_CORE_FILES = [
    "atlas_live/core/decision_knowledge_registry.py",
    "atlas_live/core/decision_outcome_tribunal.py",
    "atlas_live/core/atlas_decision_core.py",
    "atlas_live/core/knowledge_eligibility.py",
    "atlas_live/core/knowledge_eligibility_registry.py",
    "atlas_live/core/shadow_observation.py",
    "atlas_live/core/shadow_observation_registry.py",
    "atlas_live/core/activation_gate.py",
    "atlas_live/core/activation_registry.py",
    "atlas_live/core/continuous_evaluation.py",
    "atlas_live/core/continuous_evaluation_registry.py",
]

# Los 6 módulos que reverifican walk-forward de forma independiente
# (`computed_as_of < market_date`), uno por fase -- confirmado por lectura
# directa de cada uno en la auditoría de cierre de Hito 3.
_WALK_FORWARD_MODULES = {
    "3.0/learned_evidence": "atlas_live/learning/learned_evidence.py",
    "3.2/decision_outcome_tribunal": "atlas_live/core/decision_outcome_tribunal.py",
    "3.3/knowledge_eligibility": "atlas_live/core/knowledge_eligibility.py",
    "3.4/shadow_observation": "atlas_live/core/shadow_observation.py",
    "3.5/activation_gate": "atlas_live/core/activation_gate.py",
    "3.6/continuous_evaluation": "atlas_live/core/continuous_evaluation.py",
}

_FINANCIAL_VOCABULARY = ("broker", "place_order(", "execute_trade(", "buy(", "sell(")

_UNREVOKE_NAME_FRAGMENTS = ("unrevoke", "des_revocar")

# `update foo set ...` -- "update"/"set" como palabras completas, en ese
# orden, en la misma línea (mismo patrón de una sola línea que ya usa todo
# el código real para `.execute("DELETE FROM ...")`/`INSERT INTO ...`).
_RE_UPDATE_SET = re.compile(r"\bupdate\b.*\bset\b")


def _iter_py_files(roots: List[Path], exclude_tests: bool = True) -> List[Path]:
    files: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob("*.py"):
            if exclude_tests and f.name.startswith("test_"):
                continue
            if f.resolve() == _SELF_FILE:
                continue
            files.append(f)
    return files


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _strip_comments_preserving_lines(src: str) -> str:
    """Devuelve el código con cada COMMENT en blanco (mismo largo, mismas
    líneas) pero los STRING intactos -- para el chequeo de DELETE/UPDATE:
    una sentencia SQL real SIEMPRE vive dentro de un string literal
    pasado a `.execute(...)`, nunca dentro de un comentario -- así que un
    comentario que mencione "update ... set" en prosa (hallazgo real
    durante la auditoría de validación de Fase 4.2, 2026-09-04) no debe
    confundirse con una mutación real. Preserva números de línea (blanquea
    en el lugar, nunca borra líneas) para que el reporte siga siendo
    preciso. Fail-safe hacia MÁS chequeo si el archivo no tokeniza."""
    lineas = src.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            srow, scol = tok.start
            erow, ecol = tok.end
            if srow == erow:
                linea = lineas[srow - 1]
                lineas[srow - 1] = linea[:scol] + " " * (ecol - scol) + linea[ecol:]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src
    return "".join(lineas)


def _strip_strings_and_comments(src: str) -> str:
    """Devuelve el código con todo STRING (incluye docstrings) y COMMENT
    reemplazado por espacios -- para que una prosa explicativa ("nunca se
    conecta a un broker") no se confunda con uso real de vocabulario
    financiero en código ejecutable. Si el archivo no tokeniza (encoding
    raro, sintaxis inválida), devuelve la fuente tal cual -- fail-safe
    hacia MÁS chequeo, nunca hacia menos."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        piezas = []
        for tok in tokens:
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            piezas.append(tok.string)
        return " ".join(piezas)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src


def check_apply_recalibration_single_site(
    search_roots: Optional[List[Path]] = None, expected_count: int = 1,
) -> Dict[str, Any]:
    """`apply_recalibration=True` debe existir EXACTAMENTE `expected_count`
    veces en código de producción ejecutable (excluye archivos `test_*`,
    y excluye docstrings -- se usa AST, no grep, mismo criterio usado en
    cada auditoría manual de esta sesión)."""
    roots = search_roots if search_roots is not None else _DEFAULT_SEARCH_ROOTS
    sitios: List[Dict[str, Any]] = []
    for f in _iter_py_files(roots, exclude_tests=True):
        src = _read(f)
        if not src or "apply_recalibration" not in src or "True" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "apply_recalibration":
                if isinstance(node.value, ast.Constant) and node.value.value is True:
                    sitios.append({"file": str(f), "line": node.lineno})
    return {
        "ok": len(sitios) == expected_count,
        "expected_count": expected_count,
        "n_encontrados": len(sitios),
        "sitios": sitios,
    }


def check_no_auto_unrevoke(
    search_roots: Optional[List[Path]] = None, evidence_files: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Ninguna función real (AST, no grep de prosa) debe llamarse
    `unrevoke*`/`*des_revocar*` en ningún archivo de producción de TODO el
    repo (`search_roots`) -- y, específicamente en los módulos "core" de
    Hito 3 (`evidence_files`, los mismos 11 de `check_no_financial_vocabulary()`
    -- nunca el repo completo, para no confundir esto con utilidades de
    reset ajenas a Hito 3, ej. `atlas_live/learning_reset.py`, que sí
    borra datos de aprendizaje por diseño, deliberadamente, en un módulo
    fuera de esta lista), ninguna sentencia `DELETE FROM` ni `UPDATE ...
    SET` debe existir -- la revocación es permanente e irreversible por
    diseño (Fase 3.5); ni borrar ni mutar una fila ya escrita cumple esa
    garantía (corrección 2026-09-04, auditoría de validación de Fase 4.2:
    el chequeo original solo cubría DELETE, dejando pasar una mutación
    fabricada de `activation_revocation_log` vía UPDATE sin detectarla)."""
    roots = search_roots if search_roots is not None else _DEFAULT_SEARCH_ROOTS
    evidencia = evidence_files if evidence_files is not None else [_REPO_ROOT / p for p in _HITO3_CORE_FILES]
    funciones_encontradas: List[Dict[str, Any]] = []
    for f in _iter_py_files(roots, exclude_tests=True):
        src = _read(f)
        if not src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                low = node.name.lower()
                if any(frag in low for frag in _UNREVOKE_NAME_FRAGMENTS):
                    funciones_encontradas.append({"file": str(f), "line": node.lineno, "name": node.name})

    deletes_encontrados: List[Dict[str, Any]] = []
    updates_encontrados: List[Dict[str, Any]] = []
    for f in evidencia:
        f = Path(f)
        src = _read(f)
        if not src:
            continue
        sin_comentarios = _strip_comments_preserving_lines(src)
        for i, linea in enumerate(sin_comentarios.splitlines(), start=1):
            low = linea.lower()
            if "delete from" in low:
                deletes_encontrados.append({"file": str(f), "line": i, "texto": linea.strip()})
            if _RE_UPDATE_SET.search(low):
                updates_encontrados.append({"file": str(f), "line": i, "texto": linea.strip()})

    return {
        "ok": not funciones_encontradas and not deletes_encontrados and not updates_encontrados,
        "funciones_unrevoke_encontradas": funciones_encontradas,
        "delete_statements_encontrados": deletes_encontrados,
        "update_statements_encontrados": updates_encontrados,
    }


def check_no_financial_vocabulary(files: Optional[List[Path]] = None) -> Dict[str, Any]:
    """Ninguno de los archivos "core" de Hito 3 debe contener vocabulario
    de ejecución financiera real -- ni broker, ni envío de órdenes.
    Busca sobre el código EJECUTABLE únicamente (docstrings/comentarios
    despojados vía `_strip_strings_and_comments()`) -- una docstring que
    EXPLIQUE que no existe un broker (ej. `activation_registry.py`, "nunca
    se conecta a un broker") no debe confundirse con uso real (mismo
    criterio de falsos positivos ya resuelto varias veces en Hito 3)."""
    objetivo = files if files is not None else [_REPO_ROOT / p for p in _HITO3_CORE_FILES]
    hallazgos: List[Dict[str, Any]] = []
    for f in objetivo:
        src = _read(Path(f))
        if not src:
            continue
        ejecutable = _strip_strings_and_comments(src).lower()
        for palabra in _FINANCIAL_VOCABULARY:
            if palabra in ejecutable:
                hallazgos.append({"file": str(f), "palabra": palabra})
    return {"ok": not hallazgos, "archivos_revisados": [str(f) for f in objetivo], "hallazgos": hallazgos}


def _contiene_comparacion_walk_forward(tree: ast.AST) -> bool:
    """Busca un `ast.Compare` real con operador `<` cuyo lado izquierdo
    referencia `computed_as_of` y cuyo lado derecho referencia
    `market_date` -- vía `ast.unparse()` de cada operando (no de todo el
    archivo), así reconoce tanto la forma identificador
    (`computed_as_of < market_date`) como la forma clave-de-diccionario
    (`snapshot["computed_as_of"] < snapshot["market_date"]`, la que
    realmente usa `decision_outcome_tribunal.py`) -- y, al operar solo
    sobre nodos `Compare` reales del AST, es estructuralmente inmune a que
    una docstring o comentario mencione ambas palabras en prosa sin que
    exista ninguna comparación real (falso positivo confirmado y corregido
    en la auditoría de validación de Fase 4.2, 2026-09-04)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.Lt):
            continue
        try:
            izquierda = ast.unparse(node.left)
            derecha = ast.unparse(node.comparators[0])
        except Exception:
            continue
        if "computed_as_of" in izquierda and "market_date" in derecha:
            return True
    return False


_RE_SQL_WALK_FORWARD = re.compile(r"computed_as_of\s*<\s*(\?|market_date)")


def _contiene_sql_walk_forward_en_llamada(tree: ast.AST) -> bool:
    """Cubre la SEGUNDA forma real de walk-forward independiente
    encontrada en la cadena: `learned_evidence.py` no lo expresa como una
    comparación Python, sino como una cláusula SQL parametrizada
    (`... AND computed_as_of < ?`, con `market_date` pasado como parámetro
    del `.execute()`, línea 79). Busca un string literal que matchee ese
    patrón SQL, pero ÚNICAMENTE cuando ese string es un ARGUMENTO de una
    llamada real (`ast.Call.args`) -- nunca cuando es un docstring (un
    docstring es un `ast.Expr` de nivel de módulo/función/clase, nunca un
    argumento de `Call`, así que sigue siendo estructuralmente inmune a
    prosa explicativa) (hallazgo real durante la auditoría de validación
    de Fase 4.2, 2026-09-04: el detector basado solo en `Compare` dejaba
    de ver este módulo, un falso NEGATIVO)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if _RE_SQL_WALK_FORWARD.search(arg.value):
                    return True
    return False


def check_walk_forward_present_in_all_modules(
    modules: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Cada uno de los módulos de la cadena debe reverificar walk-forward
    de forma INDEPENDIENTE -- se acepta cualquiera de las 2 formas reales
    encontradas en la cadena: una comparación `Compare` de Python
    (`computed_as_of < market_date`, `snapshot["computed_as_of"] <
    snapshot["market_date"]`) o una cláusula SQL parametrizada pasada como
    argumento real de una llamada (`computed_as_of < ?`). Nunca una
    mención en prosa -- ver `_contiene_comparacion_walk_forward()`/
    `_contiene_sql_walk_forward_en_llamada()`."""
    objetivo = modules if modules is not None else _WALK_FORWARD_MODULES
    por_modulo: Dict[str, bool] = {}
    faltantes: List[str] = []
    for nombre, ruta in objetivo.items():
        src = _read(_REPO_ROOT / ruta if not Path(ruta).is_absolute() else Path(ruta))
        presente = False
        if src:
            try:
                tree = ast.parse(src)
                presente = _contiene_comparacion_walk_forward(tree) or _contiene_sql_walk_forward_en_llamada(tree)
            except SyntaxError:
                presente = False
        por_modulo[nombre] = presente
        if not presente:
            faltantes.append(nombre)
    return {"ok": not faltantes, "por_modulo": por_modulo, "modulos_sin_walk_forward": faltantes}


def run_all_checks() -> Dict[str, Any]:
    """Agrega los 4 checks contra el repo REAL (nunca acepta overrides --
    ver los tests de cada check individual para los casos de fixture
    sintética). Nunca lanza -- cualquier excepción de un check individual
    queda contenida y ese check se reporta `ok=False` con el error."""
    resultados: Dict[str, Any] = {}
    checks = {
        "apply_recalibration_single_site": check_apply_recalibration_single_site,
        "no_auto_unrevoke": check_no_auto_unrevoke,
        "no_financial_vocabulary": check_no_financial_vocabulary,
        "walk_forward_present_in_all_modules": check_walk_forward_present_in_all_modules,
    }
    for nombre, fn in checks.items():
        try:
            resultados[nombre] = fn()
        except Exception as exc:
            resultados[nombre] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": all(r.get("ok") for r in resultados.values()),
        "checks": resultados,
    }
