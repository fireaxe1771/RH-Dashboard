/**
 * Utilities for exporting tabular widget data to CSV and Excel formats.
 *
 * No external dependencies are required:
 *  - CSV files are emitted as UTF-8 with a BOM so Excel auto-detects encoding.
 *  - Excel (.xls) files use the well-established HTML-table wrapper that
 *    Microsoft Excel opens natively, avoiding the need for a heavy xlsx
 *    library in the static frontend bundle.
 */

/** Coerce a cell value into a display string safe for export. */
function cellToString(val: unknown): string {
  if (val === null || val === undefined) return '';
  if (typeof val === 'number' && !Number.isFinite(val)) return '';
  return String(val);
}

/**
 * Convert a grid of columns + rows into RFC-4180-style CSV text.
 * Fields containing commas, quotes, or newlines are double-quoted and
 * inner quotes are escaped per the CSV specification.
 */
function buildCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const escape = (s: string): string => {
    if (/[",\n\r]/.test(s)) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  };

  const lines: string[] = [columns.map((c) => escape(c)).join(',')];
  for (const row of rows) {
    lines.push(columns.map((c) => escape(cellToString(row[c]))).join(','));
  }
  return lines.join('\r\n');
}

/** Trigger a browser download for a Blob with the given filename. */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  // Revoke on the next tick so the download has time to start.
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

/** Sanitise a widget title into a filesystem-safe base filename. */
function safeBaseName(title: string): string {
  const cleaned = title.replace(/[^a-zA-Z0-9 _\-]/g, '').trim().replace(/\s+/g, '_');
  return cleaned || 'export';
}

/**
 * Export the supplied columns/rows as a UTF-8 CSV file (Excel-compatible).
 */
export function exportToCsv(
  title: string,
  columns: string[],
  rows: Record<string, unknown>[],
): void {
  const csv = buildCsv(columns, rows);
  // Prepend BOM so Excel interprets the file as UTF-8.
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
  downloadBlob(blob, `${safeBaseName(title)}.csv`);
}

/**
 * Export the supplied columns/rows as an Excel (.xls) file using an
 * HTML-table wrapper.  Excel opens this format directly without requiring
 * a binary xlsx encoder.
 */
export function exportToExcel(
  title: string,
  columns: string[],
  rows: Record<string, unknown>[],
): void {
  const escapeHtml = (s: string): string =>
    s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  const headerCells = columns.map((c) => `<th>${escapeHtml(c)}</th>`).join('');
  const bodyRows = rows
    .map(
      (row) =>
        `<tr>${columns
          .map((c) => `<td>${escapeHtml(cellToString(row[c]))}</td>`)
          .join('')}</tr>`,
    )
    .join('');

  const html = (
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" ' +
    'xmlns:x="urn:schemas-microsoft-com:office:excel" ' +
    'xmlns="http://www.w3.org/TR/REC-html40">' +
    '<head><meta charset="utf-8"><!--[if gte mso 9]>' +
    '<xml><x:ExcelWorkbook><x:ExcelWorksheets><x:ExcelWorksheet>' +
    `<x:Name>${escapeHtml(title)}</x:Name>` +
    '<x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions>' +
    '</x:ExcelWorksheet></x:ExcelWorksheets></x:ExcelWorkbook></x:xml>' +
    '<![endif]--></head><body>' +
    `<table border="1"><thead><tr>${headerCells}</tr></thead>` +
    `<tbody>${bodyRows}</tbody></table>` +
    '</body></html>'
  );

  const blob = new Blob(['\uFEFF' + html], {
    type: 'application/vnd.ms-excel;charset=utf-8;',
  });
  downloadBlob(blob, `${safeBaseName(title)}.xls`);
}
