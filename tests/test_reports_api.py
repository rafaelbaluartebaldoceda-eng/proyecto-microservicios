import pytest


@pytest.mark.asyncio
async def test_report_creation_status_and_download(client, auth_headers):
    payload = {
        "report_type": "sales_summary",
        "format": "excel",
        "filters": {
            "start_date": "2026-04-01",
            "end_date": "2026-04-10",
            "area": "Finanzas",
            "status": "closed",
            "category": "Q2",
            "requested_user": "ana",
        },
    }
    create_response = await client.post("/reports", headers=auth_headers, json=payload)
    assert create_response.status_code == 202
    report_id = create_response.json()["report_id"]

    detail_response = await client.get(f"/reports/{report_id}", headers=auth_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "SUCCESS"

    download_response = await client.get(f"/reports/{report_id}/download", headers=auth_headers)
    assert download_response.status_code == 200
    download_url = download_response.json()["download_url"]

    file_response = await client.get(download_url, headers=auth_headers)
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.asyncio
async def test_list_reports(client, auth_headers):
    response = await client.get("/reports", headers=auth_headers)
    assert response.status_code == 200
    assert "items" in response.json()
