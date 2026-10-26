from html import unescape
from html.parser import HTMLParser
import json
import re

from video_checklist.export import html_report


def test_foundry_export_preserves_provenance_without_executable_input_markup():
    report = {
        "source": "azure", "analysis_provider": "azure_foundry_responses",
        "schema_version": "test-schema", "api_version": "v1",
        "model": "gpt-6.1-sol", "model_version": "test-only",
        "clip": {"name": "<script>input()</script>.mp4", "hash": "a" * 64, "duration_ms": 1000, "license": "Grabación de prueba con permiso."},
        "frame_sampling": {"fps": 2, "actual_frame_count": 3, "max_edge_px": 1280, "timestamps_ms": [0, 500, 1000]},
        "checklist": [], "events": [], "issues": [],
        "limitations": ["Los tiempos son estimaciones sobre fotogramas; no grounding del servicio."],
        "review": {"notes": "Revisión humana separada", "decisions": []},
    }
    html = html_report(report)
    assert "Proveedor: Microsoft Foundry · Azure Responses" in html
    assert "API: v1" in html and "Versión del modelo: test-only" in html
    assert "2 fotogramas/s · 3 fotogramas analizados · 1280 px de lado máximo" in html
    assert "no acreditan grounding temporal del servicio" in html
    assert "<script>input()</script>" not in html
    embedded = json.loads(unescape(re.search(r"<pre>(.*?)</pre>", html, re.S).group(1)))
    assert embedded == report


def test_observed_step_retains_visible_uncertainty_for_its_second_interval():
    events = [
        {"id": "first", "step_id": "S2", "start_ms": 800, "end_ms": 1560, "time_source": "model_estimate", "evidence_status": "observed"},
        {"id": "second", "step_id": "S2", "start_ms": 15440, "end_ms": 16200, "time_source": "model_estimate", "evidence_status": "uncertain"},
    ]
    report = {
        "source": "azure", "analysis_provider": "azure_foundry_responses",
        "schema_version": "unchanged-test-schema", "api_version": "v1", "model": "gpt-6.1-sol",
        "clip": {"name": "reordered.mp4", "hash": "a" * 64, "duration_ms": 37000, "license": "Test fixture only."},
        "events": events,
        "checklist": [{"id": "S2", "label": "Introducir la cuerda", "status": "observed", "events": ["first", "second"]}],
        "issues": [], "review": {"notes": "", "decisions": []},
    }

    class VisibleTable(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_table = False
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.in_table = True

        def handle_endtag(self, tag):
            if tag == "table":
                self.in_table = False

        def handle_data(self, data):
            if self.in_table:
                self.parts.append(data)

    before = json.dumps(report, sort_keys=True)
    html = html_report(report)
    table = VisibleTable()
    table.feed(html)
    visible = "".join(table.parts)
    assert "800–1560 ms (model_estimate) · Observado" in visible
    assert "15440–16200 ms (model_estimate) · Incierto" in visible
    assert "observed" in visible  # The step remains observed; only its second event is uncertain.
    assert json.dumps(report, sort_keys=True) == before
    embedded = json.loads(unescape(re.search(r"<pre>(.*?)</pre>", html, re.S).group(1)))
    assert embedded == report
