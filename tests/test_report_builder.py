from uuid import uuid4

from app.models.report import ReportFormat, ReportType
from app.services.report_builder import ReportBuilderService


def test_builder_generates_excel():
    builder = ReportBuilderService()
    report = builder.build_report(
        report_id=uuid4(),
        report_type=ReportType.sales_summary,
        report_format=ReportFormat.excel,
        filters={"start_date": "2026-04-01", "end_date": "2026-04-10", "area": "Finanzas", "category": "Q2"},
    )
    assert report.file_name.endswith(".xlsx")
    assert report.payload
    assert report.row_count > 0


def test_builder_generates_pdf():
    builder = ReportBuilderService()
    report = builder.build_report(
        report_id=uuid4(),
        report_type=ReportType.audit_log,
        report_format=ReportFormat.pdf,
        filters={"start_date": "2026-04-01", "end_date": "2026-04-10", "area": "Seguridad", "category": "Incidentes"},
    )
    assert report.file_name.endswith(".pdf")
    assert report.payload.startswith(b"%PDF")
