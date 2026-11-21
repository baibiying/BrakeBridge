import csv
import io
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from zipfile import BadZipFile

MAX_ROWS = 1000


def normalize(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value).strip()


def rows_from_upload(upload, required, excel=False, aliases=None):
    if upload is None or not upload.filename:
        raise ValueError("请选择文件。")
    filename = upload.filename.lower()
    records = None
    aliases = aliases or {}

    def map_headers(names):
        keys = [normalize(name).lower() for name in names]
        return [aliases.get(key, key) for key in keys]

    try:
        if filename.endswith(".csv"):
            raw = upload.read()
            try:
                content = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                content = raw.decode("gb18030")
            reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
            headers = map_headers(reader.fieldnames or ())
            reader.fieldnames = headers
            records = reader
        elif excel and filename.endswith(".xlsx"):
            workbook = load_workbook(upload.stream, read_only=True, data_only=True)
            try:
                sheet = workbook.worksheets[0]
                if sheet.max_column and sheet.max_column > 50:
                    raise ValueError("第一张表最多支持 50 列。")
                rows = sheet.iter_rows(values_only=True)
                headers = map_headers(next(rows, ()))
                records = collect(
                    (dict(zip(headers, row)) for row in rows), headers, required
                )
                return records
            finally:
                workbook.close()
        else:
            raise ValueError("请上传 CSV 或 Excel (.xlsx)。" if excel else "请上传 CSV。")
        return collect(records, headers, required)
    except (csv.Error, UnicodeDecodeError, InvalidFileException, BadZipFile, KeyError) as error:
        raise ValueError("表格读取失败，请用模板重新保存后上传。") from error


def collect(records, headers, required):
    if len(set(h for h in headers if h)) != len([h for h in headers if h]):
        raise ValueError("表头有重复字段。")
    missing = set(required) - set(headers)
    if missing:
        raise ValueError("缺少表头 " + "、".join(sorted(missing)) + "。")
    result = []
    for line, row in enumerate(records, 2):
        if line > MAX_ROWS + 1:
            raise ValueError("一次最多导入 1,000 行。")
        if None in row:
            result.append((line, {}))
            continue
        clean = {key: normalize(value) for key, value in row.items() if key}
        if any(clean.values()):
            result.append((line, clean))
    return result


def csv_bytes(headers, rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([spreadsheet_text(value) for value in row])
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def spreadsheet_text(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
