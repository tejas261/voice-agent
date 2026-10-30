import subprocess
import sys

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from scripts.convert_document import convert
from src.app import create_app
from src.documents import load_documents


def make_pdf(path, text=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    if text:
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({
                NameObject('/F1'): DictionaryObject({
                    NameObject('/Type'): NameObject('/Font'),
                    NameObject('/Subtype'): NameObject('/Type1'),
                    NameObject('/BaseFont'): NameObject('/Helvetica'),
                })
            })
        })
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 350 Td (We are open from nine to five.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as target:
        writer.write(target)


def test_pdf_conversion_is_importable_by_business_workspace(tmp_path, settings):
    pdf = tmp_path / 'hours.pdf'
    make_pdf(pdf)
    output = tmp_path / 'hours.txt'
    process = subprocess.run([sys.executable, 'scripts/convert_document.py', str(pdf), '-o', str(output)],
                             capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
    assert 'open from nine to five' in output.read_text()
    with TestClient(create_app(settings)) as client:
        session = client.post('/api/auth/signup', json={'email':'owner@example.com','password':'long-enough-password'}).json()
        business = client.post('/api/onboarding/business', headers={'X-CSRF-Token':session['csrf']}, json={
            'name':'Cafe', 'kind':'restaurant', 'greeting':'Hello',
        }).json()['business']
        with output.open('rb') as file:
            response = client.post(f"/api/businesses/{business['id']}/knowledge/import", headers={'X-CSRF-Token':session['csrf']},
                                   files={'file':(output.name, file, 'text/plain')})
        assert response.status_code == 200, response.text
        assert 'open from nine to five' in response.json()['entries'][-1]['answer_en']


def test_docx_tables_headers_and_policy_format(tmp_path):
    source = tmp_path / 'clinic.docx'
    document = Document()
    document.add_heading('Clinic hours', 1)
    document.add_paragraph('Appointments are required before visiting.')
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = 'Monday'
    table.cell(0, 1).text = '9 am to 5 pm'
    document.sections[0].header.paragraphs[0].text = 'Green Clinic'
    document.save(source)
    output = tmp_path / 'clinic.txt'
    _, count, warnings = convert(source, output)
    contents = output.read_text()
    assert count and 'Clinic hours' in contents and 'Appointments are required' in contents
    assert 'Monday | 9 am to 5 pm' in contents and 'Green Clinic' in contents
    assert any('Headers and footers' in warning for warning in warnings)
    policy = tmp_path / 'policy.txt'
    convert(source, policy, mode='policy')
    entries, _ = load_documents(policy)
    assert any('Appointments are required' in entry.answer_en for entry in entries)
    assert any('Monday | 9 am to 5 pm' in entry.answer_en for entry in entries)


def test_large_unicode_document_is_split_without_losing_words(tmp_path):
    source = tmp_path / 'long.txt'
    words = [f'बात{i}' for i in range(9000)]
    source.write_text(' '.join(words), encoding='utf-8')
    paths, count, warnings = convert(source, tmp_path / 'long.kb.txt')
    assert len(paths) > 1 and count > 39
    assert any('40 entries' in warning for warning in warnings)
    assert all(path.stat().st_size <= 24_000 for path in paths)
    assert all(len(part) <= 1200 for path in paths for part in path.read_text().strip().split('\n\n'))
    assert ' '.join(path.read_text() for path in paths).split() == words


def test_multi_file_conversion_imports_all_sections(tmp_path, settings):
    source = tmp_path / 'business-guide.txt'
    source.write_text('\n\n'.join(f'Section {index}. ' + ('Useful details. ' * 58) for index in range(30)), encoding='utf-8')
    paths, count, warnings = convert(source, tmp_path / 'guide.txt')
    assert len(paths) > 1 and count <= 39 and not warnings
    with TestClient(create_app(settings)) as client:
        session = client.post('/api/auth/signup', json={'email':'guide@example.com','password':'long-enough-password'}).json()
        headers = {'X-CSRF-Token':session['csrf']}
        business = client.post('/api/onboarding/business', headers=headers, json={
            'name':'Cafe', 'kind':'restaurant', 'greeting':'Hello',
        }).json()['business']
        for path in paths:
            with path.open('rb') as file:
                response = client.post(f"/api/businesses/{business['id']}/knowledge/import", headers=headers,
                                       files={'file':(path.name, file, 'text/plain')})
            assert response.status_code == 200, response.text
        entries = response.json()['entries']
        assert len(entries) == count + 1
        assert 'Section 0.' in entries[1]['answer_en'] and 'Section 29.' in entries[-1]['answer_en']


def test_no_text_unsupported_and_overwrite_are_reported(tmp_path):
    pdf = tmp_path / 'scan.pdf'
    make_pdf(pdf, text=False)
    with pytest.raises(ValueError, match='No text'):
        convert(pdf, tmp_path / 'scan.txt')
    with pytest.raises(ValueError, match='Supported inputs'):
        convert(tmp_path / 'unknown.doc', tmp_path / 'out.txt')
    invalid = tmp_path / 'invalid.docx'
    invalid.write_text('Not a DOCX file')
    with pytest.raises(ValueError, match='Could not read this DOCX'):
        convert(invalid, tmp_path / 'out.txt')
    source = tmp_path / 'original.txt'
    source.write_text('Keep this source.', encoding='utf-8')
    with pytest.raises(ValueError, match='source document'):
        convert(source, source, force=True)
    output = tmp_path / 'out.txt'
    output.write_text('Do not overwrite.', encoding='utf-8')
    with pytest.raises(ValueError, match='already exists'):
        convert(source, output)
    assert output.read_text() == 'Do not overwrite.'
