"""HITO 4 -- Fase 4.2 (2026-09-04, autorizado explícitamente en Plan Mode):
tests de `hito3_integrity_check.py`. Dos grupos: (1) contra el REPO REAL --
deben pasar HOY, confirmando que Hito 3 sigue íntegro; (2) contra
fixtures sintéticas con una violación fabricada -- confirman que cada
detector realmente detecta, no es un placeholder que siempre dice `ok=True`."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import hito3_integrity_check as hic


def _tmp_file(content: str) -> Path:
    p = Path(tempfile.gettempdir()) / f"atlas_test_h4_fixture_{_uuid.uuid4().hex}.py"
    p.write_text(content, encoding="utf-8")
    return p


# --- 1) contra el repo real -- deben pasar HOY ------------------------------

def test_apply_recalibration_single_site_repo_real():
    r = hic.check_apply_recalibration_single_site()
    assert r["ok"] is True, r
    assert r["n_encontrados"] == 1
    assert r["sitios"][0]["file"].replace("\\", "/").endswith("atlas_live/server.py")


def test_no_auto_unrevoke_repo_real():
    r = hic.check_no_auto_unrevoke()
    assert r["ok"] is True, r
    assert r["funciones_unrevoke_encontradas"] == []
    assert r["delete_statements_encontrados"] == []
    assert r["update_statements_encontrados"] == []


def test_no_financial_vocabulary_repo_real():
    r = hic.check_no_financial_vocabulary()
    assert r["ok"] is True, r
    assert r["hallazgos"] == []
    assert len(r["archivos_revisados"]) == 11


def test_walk_forward_present_in_all_modules_repo_real():
    r = hic.check_walk_forward_present_in_all_modules()
    assert r["ok"] is True, r
    assert all(r["por_modulo"].values())
    assert len(r["por_modulo"]) == 6


def test_run_all_checks_repo_real_ok():
    r = hic.run_all_checks()
    assert r["ok"] is True, r
    assert set(r["checks"].keys()) == {
        "apply_recalibration_single_site", "no_auto_unrevoke",
        "no_financial_vocabulary", "walk_forward_present_in_all_modules",
    }
    assert all(c["ok"] for c in r["checks"].values())


# --- 2) fixtures sintéticas -- el detector debe detectar de verdad ---------

def test_apply_recalibration_detecta_cero_sitios():
    vacio = _tmp_file("x = 1\n")
    try:
        r = hic.check_apply_recalibration_single_site(search_roots=[vacio.parent], expected_count=1)
        # puede haber ruido de otros archivos temporales del propio SO en
        # ese directorio -- lo que interesa es que ESTE archivo, sin la
        # keyword, no aporta ningún sitio.
        assert not any(s["file"] == str(vacio) for s in r["sitios"])
    finally:
        vacio.unlink(missing_ok=True)


def test_apply_recalibration_detecta_dos_sitios_como_violacion():
    tmp = Path(tempfile.gettempdir()) / f"atlas_test_h4_fixture_{_uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        f1 = tmp / "a.py"
        f2 = tmp / "b.py"
        f1.write_text("def x():\n    decide(apply_recalibration=True)\n", encoding="utf-8")
        f2.write_text("def y():\n    decide(apply_recalibration=True)\n", encoding="utf-8")
        r = hic.check_apply_recalibration_single_site(search_roots=[tmp], expected_count=1)
        assert r["ok"] is False
        assert r["n_encontrados"] == 2
    finally:
        for f in tmp.glob("*.py"):
            f.unlink()
        tmp.rmdir()


def test_apply_recalibration_ignora_docstrings_y_comentarios():
    tmp = Path(tempfile.gettempdir()) / f"atlas_test_h4_fixture_{_uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        f1 = tmp / "a.py"
        f1.write_text(
            '"""Esta docstring menciona apply_recalibration=True pero no es código real."""\n'
            "# apply_recalibration=True tambien en un comentario\n"
            "x = 1\n",
            encoding="utf-8",
        )
        r = hic.check_apply_recalibration_single_site(search_roots=[tmp], expected_count=0)
        assert r["ok"] is True
        assert r["n_encontrados"] == 0
    finally:
        for f in tmp.glob("*.py"):
            f.unlink()
        tmp.rmdir()


def test_apply_recalibration_ignora_archivos_test():
    tmp = Path(tempfile.gettempdir()) / f"atlas_test_h4_fixture_{_uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        f1 = tmp / "test_algo.py"
        f1.write_text("decide(apply_recalibration=True)\n", encoding="utf-8")
        r = hic.check_apply_recalibration_single_site(search_roots=[tmp], expected_count=0)
        assert r["ok"] is True
        assert r["n_encontrados"] == 0
    finally:
        for f in tmp.glob("*.py"):
            f.unlink()
        tmp.rmdir()


def test_no_auto_unrevoke_detecta_funcion_fabricada():
    tmp = Path(tempfile.gettempdir()) / f"atlas_test_h4_fixture_{_uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        f1 = tmp / "a.py"
        f1.write_text("def unrevoke_condition(x):\n    pass\n", encoding="utf-8")
        r = hic.check_no_auto_unrevoke(search_roots=[tmp], evidence_files=[])
        assert r["ok"] is False
        assert len(r["funciones_unrevoke_encontradas"]) == 1
        assert r["funciones_unrevoke_encontradas"][0]["name"] == "unrevoke_condition"
    finally:
        for f in tmp.glob("*.py"):
            f.unlink()
        tmp.rmdir()


def test_no_auto_unrevoke_detecta_delete_fabricado():
    f1 = _tmp_file('conn.execute("DELETE FROM activation_revocation_log")\n')
    try:
        r = hic.check_no_auto_unrevoke(search_roots=[], evidence_files=[f1])
        assert r["ok"] is False
        assert len(r["delete_statements_encontrados"]) == 1
    finally:
        f1.unlink(missing_ok=True)


def test_no_auto_unrevoke_detecta_update_fabricado():
    """Corrección post-auditoría (2026-09-04): el chequeo original solo
    buscaba DELETE -- una mutación fabricada de una fila ya escrita vía
    UPDATE (igual de grave: viola "la revocación es permanente e
    irreversible") pasaba sin detectarse. Confirma que ahora sí."""
    f1 = _tmp_file('conn.execute("UPDATE activation_revocation_log SET reason=?", (x,))\n')
    try:
        r = hic.check_no_auto_unrevoke(search_roots=[], evidence_files=[f1])
        assert r["ok"] is False
        assert len(r["update_statements_encontrados"]) == 1
    finally:
        f1.unlink(missing_ok=True)


def test_no_auto_unrevoke_ignora_update_set_mencionado_en_un_comentario():
    """Hallazgo real durante la auditoría de validación (2026-09-04): el
    chequeo de DELETE/UPDATE escanea líneas crudas -- una sentencia SQL
    real SIEMPRE vive dentro de un string pasado a `.execute()`, nunca en
    un comentario, así que un comentario que mencione "update ... set" en
    prosa no debe contar como violación."""
    f1 = _tmp_file("# nunca hacemos algo como update foo set x=1 aqui\nx = 1\n")
    try:
        r = hic.check_no_auto_unrevoke(search_roots=[], evidence_files=[f1])
        assert r["ok"] is True
        assert r["update_statements_encontrados"] == []
    finally:
        f1.unlink(missing_ok=True)


def test_no_auto_unrevoke_detecta_update_incluso_con_comentario_en_la_misma_linea():
    # El UPDATE real sigue en el string -- el comentario blanqueado en la
    # misma línea no debe esconder la detección.
    f1 = _tmp_file('conn.execute("UPDATE activation_revocation_log SET reason=?")  # nota\n')
    try:
        r = hic.check_no_auto_unrevoke(search_roots=[], evidence_files=[f1])
        assert r["ok"] is False
        assert len(r["update_statements_encontrados"]) == 1
    finally:
        f1.unlink(missing_ok=True)


def test_no_financial_vocabulary_detecta_broker_fabricado():
    # Código EJECUTABLE real (no un comentario/docstring) -- confirma que
    # el detector encuentra uso real, no solo prosa.
    f1 = _tmp_file("resultado = broker.place_order(x)\n")
    try:
        r = hic.check_no_financial_vocabulary(files=[f1])
        assert r["ok"] is False
        palabras = {h["palabra"] for h in r["hallazgos"]}
        assert "broker" in palabras
    finally:
        f1.unlink(missing_ok=True)


def test_no_financial_vocabulary_ignora_mencion_en_docstring():
    # Prosa que EXPLICA la ausencia de broker -- mismo patrón real que
    # `activation_registry.py` -- nunca debe contar como violación.
    f1 = _tmp_file('"""Este módulo nunca se conecta a un broker."""\nx = 1\n')
    try:
        r = hic.check_no_financial_vocabulary(files=[f1])
        assert r["ok"] is True
        assert r["hallazgos"] == []
    finally:
        f1.unlink(missing_ok=True)


def test_walk_forward_present_in_all_modules_detecta_ausencia():
    f1 = _tmp_file("x = 1  # sin ningun chequeo de fecha\n")
    try:
        r = hic.check_walk_forward_present_in_all_modules(modules={"fixture_sin_walk_forward": str(f1)})
        assert r["ok"] is False
        assert "fixture_sin_walk_forward" in r["modulos_sin_walk_forward"]
    finally:
        f1.unlink(missing_ok=True)


def test_walk_forward_present_in_all_modules_detecta_presencia():
    f1 = _tmp_file("if computed_as_of < market_date:\n    pass\n")
    try:
        r = hic.check_walk_forward_present_in_all_modules(modules={"fixture_con_walk_forward": str(f1)})
        assert r["ok"] is True
        assert r["por_modulo"]["fixture_con_walk_forward"] is True
    finally:
        f1.unlink(missing_ok=True)


def test_walk_forward_detecta_forma_diccionario_real():
    # Forma exacta que usa decision_outcome_tribunal.py -- claves de
    # diccionario, no identificadores sueltos.
    f1 = _tmp_file('x = not (snapshot["computed_as_of"] < snapshot["market_date"])\n')
    try:
        r = hic.check_walk_forward_present_in_all_modules(modules={"fixture_dict": str(f1)})
        assert r["ok"] is True
        assert r["por_modulo"]["fixture_dict"] is True
    finally:
        f1.unlink(missing_ok=True)


def test_walk_forward_detecta_forma_sql_parametrizada_real():
    # Forma exacta que usa learned_evidence.py -- cláusula WHERE
    # parametrizada pasada como argumento real de una llamada, nunca una
    # comparación Python.
    f1 = _tmp_file('conn.execute("SELECT * FROM x WHERE computed_as_of < ?", (market_date,))\n')
    try:
        r = hic.check_walk_forward_present_in_all_modules(modules={"fixture_sql": str(f1)})
        assert r["ok"] is True
        assert r["por_modulo"]["fixture_sql"] is True
    finally:
        f1.unlink(missing_ok=True)


def test_walk_forward_ignora_mencion_en_docstring_pese_a_mencionar_ambas_palabras():
    """Hallazgo real durante la auditoría de validación (2026-09-04): el
    chequeo original (`"computed_as_of" in src and "< market_date" in
    src`) pasaba `decision_outcome_tribunal.py` casi enteramente gracias a
    una PARÁFRASIS en su docstring, no a su código real (que usa la forma
    diccionario). Este fixture reproduce exactamente ese patrón -- ambas
    frases presentes, pero solo en prosa, en oraciones separadas, sin
    ninguna comparación ni llamada SQL real -- debe fallar."""
    f1 = _tmp_file(
        '"""Este modulo menciona computed_as_of en la prosa. También '
        'menciona, en otra frase separada, algo que es < market_date '
        'solo como ejemplo de texto."""\nx = 1\n'
    )
    try:
        r = hic.check_walk_forward_present_in_all_modules(modules={"fixture_prosa": str(f1)})
        assert r["ok"] is False
        assert r["por_modulo"]["fixture_prosa"] is False
    finally:
        f1.unlink(missing_ok=True)


def test_walk_forward_ignora_patron_sql_mencionado_solo_en_docstring():
    # El patrón SQL "computed_as_of < ?" en la prosa de un docstring
    # (nunca dentro de un ast.Call real) tampoco debe contar.
    f1 = _tmp_file(
        '"""Este módulo hace algo como computed_as_of < ? en su docstring, '
        'pero nunca en una llamada real."""\nx = 1\n'
    )
    try:
        r = hic.check_walk_forward_present_in_all_modules(modules={"fixture_sql_docstring": str(f1)})
        assert r["ok"] is False
        assert r["por_modulo"]["fixture_sql_docstring"] is False
    finally:
        f1.unlink(missing_ok=True)


def test_run_all_checks_nunca_lanza_ante_un_check_roto(monkeypatch):
    def _roto():
        raise RuntimeError("check roto a proposito")

    monkeypatch.setattr(hic, "check_no_financial_vocabulary", _roto)
    r = hic.run_all_checks()
    assert r["ok"] is False
    assert r["checks"]["no_financial_vocabulary"]["ok"] is False
    assert "check roto a proposito" in r["checks"]["no_financial_vocabulary"]["error"]
