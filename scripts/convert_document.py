#!/usr/bin/env python3
"""Convert local documents into the formats accepted by Listen's knowledge bases."""

import argparse
import re
import sys
from pathlib import Path
from zipfile import BadZipFile

MAX_PARAGRAPH = 1200
MAX_FILE_BYTES = 24_000


def _text(value):
    return re.sub(r'\s+', ' ', value).strip()


def _docx_blocks(container):
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    blocks = []
    for item in container.iter_inner_content():
        if isinstance(item, Paragraph):
            value = _text(item.text)
            if value:
                blocks.append(value)
        elif isinstance(item, Table):
            for row in item.rows:
                cells = [_text(cell.text) for cell in row.cells]
                if any(cells):
                    blocks.append(' | '.join(cells))
    return blocks


def extract(path):
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.md'}:
        return [part for part in re.split(r'\n\s*\n', path.read_text(encoding='utf-8-sig')) if part.strip()], []
    if suffix == '.pdf':
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('This PDF is encrypted. Remove its password before converting it.')
            blocks, missing = [], []
            for number, page in enumerate(reader.pages, 1):
                content = page.extract_text() or ''
                if not content.strip():
                    missing.append(number)
                    continue
                paragraphs = [p.strip() for p in re.split(r'\n\s*\n', content) if p.strip()]
                blocks.extend(paragraphs)
        except PyPdfError as exc:
            raise ValueError('Could not read this PDF; check that the file is valid.') from exc
        warnings = []
        if missing:
            warnings.append('No text was extracted from PDF page(s) ' + ', '.join(map(str, missing)) + '. They may be blank or image-only; OCR is not included.')
        return blocks, warnings
    if suffix == '.docx':
        from docx import Document
        from docx.opc.exceptions import PackageNotFoundError

        try:
            document = Document(path)
        except (PackageNotFoundError, BadZipFile) as exc:
            raise ValueError('Could not read this DOCX; check that the file is valid.') from exc
        blocks = _docx_blocks(document)
        extras, seen = [], set()
        for index, section in enumerate(document.sections, 1):
            for label, part in (('Header', section.header), ('Footer', section.footer),
                                ('First-page header', section.first_page_header), ('First-page footer', section.first_page_footer),
                                ('Even-page header', section.even_page_header), ('Even-page footer', section.even_page_footer)):
                if part.is_linked_to_previous or part.part.partname in seen:
                    continue
                seen.add(part.part.partname)
                values = _docx_blocks(part)
                if values:
                    extras.extend([f'{label}, section {index}:', *values])
        warnings = []
        if document.element.xpath('.//*[local-name()="drawing" or local-name()="pict"]'):
            warnings.append('The DOCX contains images or drawings; text inside images is not extracted.')
        parts = {str(part.partname) for part in document.part.package.parts}
        if '/word/footnotes.xml' in parts or '/word/endnotes.xml' in parts:
            warnings.append('The DOCX contains footnotes or endnotes; review them separately.')
        if extras:
            blocks.extend(extras)
            warnings.append('Headers and footers were included at the end; check their context.')
        return blocks, warnings
    raise ValueError('Supported inputs are PDF, DOCX, TXT and Markdown files.')


def split_blocks(paragraphs):
    """Keep every word, preferring sentence and word boundaries over hard cuts."""
    result, current = [], ''
    for original in paragraphs:
        paragraph = _text(original)
        while paragraph:
            available = MAX_PARAGRAPH - len(current) - (1 if current else 0)
            if available <= 0:
                result.append(current)
                current = ''
                continue
            if len(paragraph) <= available:
                current += ('\n' if current else '') + paragraph
                break
            if current and available < 300:
                result.append(current)
                current = ''
                continue
            boundary = max((m.end() for m in re.finditer(r'[.!?](?:["\'”’)]*)\s+', paragraph[:available + 1]) if m.end() >= available // 2), default=0)
            if not boundary:
                boundary = paragraph.rfind(' ', 0, available + 1)
            if boundary <= 0:
                boundary = available
            part, paragraph = paragraph[:boundary].strip(), paragraph[boundary:].strip()
            if part:
                current += ('\n' if current else '') + part
            if current:
                result.append(current)
                current = ''
    if current:
        result.append(current)
    return result


def pack_files(blocks):
    files, current = [], []
    for block in blocks:
        candidate = '\n\n'.join([*current, block]) + '\n'
        if len(candidate.encode('utf-8')) > MAX_FILE_BYTES and current:
            files.append('\n\n'.join(current) + '\n')
            current = [block]
        else:
            current.append(block)
    if current:
        files.append('\n\n'.join(current) + '\n')
    return files


def convert(source, output, mode='business', force=False):
    paragraphs, warnings = extract(source)
    blocks = split_blocks(paragraphs)
    if not blocks:
        raise ValueError('No text could be extracted. Scanned documents need OCR before conversion.')
    if mode == 'policy':
        name = _text(source.name)
        contents = ['SOURCE_DOCUMENT: ' + name + '\nSECTION: DOCUMENT\n' + ''.join(
            f'CLAUSE_{i:05d}: {re.sub(r"\s+", " ", block).strip()}\n' for i, block in enumerate(blocks, 1))]
    else:
        contents = pack_files(blocks)
        if len(blocks) > 39:
            warnings.append(f'This document has {len(blocks)} sections. A workspace currently allows 40 entries total, including existing entries; it may not fit as a whole.')
    paths = [output] if len(contents) == 1 else [output.with_name(f'{output.stem}.part-{i:03d}.txt') for i in range(1, len(contents) + 1)]
    for path in paths:
        if len(path.name) > 70:
            raise ValueError('Output filename must be at most 70 characters; choose a shorter name.')
        if path.resolve() == source.resolve():
            raise ValueError('Output must not overwrite the source document.')
        if path.exists() and not force:
            raise ValueError(f'{path} already exists; choose another output or use --force.')
        if not path.parent.is_dir():
            raise ValueError(f'Output directory does not exist: {path.parent}')
    for path, content in zip(paths, contents, strict=True):
        path.write_text(content, encoding='utf-8')
    return paths, len(blocks), warnings


def main():
    parser = argparse.ArgumentParser(description='Convert PDF, DOCX, TXT or Markdown into Listen knowledge-base text.')
    parser.add_argument('source', type=Path, help='Document to convert')
    parser.add_argument('--output', '-o', type=Path, help='Destination .txt file (default: SOURCE.kb.txt)')
    parser.add_argument('--format', choices=('business', 'policy'), default='business', help='Business importer (default), or LABEL: text for the policy playground')
    parser.add_argument('--force', action='store_true', help='Overwrite existing output files')
    args = parser.parse_args()
    output = args.output or args.source.with_name(args.source.stem + '.kb.txt')
    if output.suffix.lower() != '.txt':
        parser.error('Output must have a .txt extension')
    try:
        paths, count, warnings = convert(args.source, output, args.format, args.force)
    except (OSError, UnicodeError, ValueError, ImportError, BadZipFile) as exc:
        print(f'Conversion failed: {exc}', file=sys.stderr)
        return 1
    print(f'Converted {count} section(s):')
    for path in paths:
        print(f'  {path}')
    for warning in warnings:
        print('Warning:', warning, file=sys.stderr)
    print('Review the extracted text and its conditions before importing.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
