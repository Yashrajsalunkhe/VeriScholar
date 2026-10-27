import { useMemo } from 'react';

interface HighlightedTextProps {
  text: string;
  passages: [number, number][];
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export default function HighlightedText({ text, passages }: HighlightedTextProps) {
  const html = useMemo(() => {
    if (!passages.length) return escapeHtml(text);
    let result = '';
    let cursor = 0;
    for (const [start, end] of passages) {
      if (start > cursor) {
        result += escapeHtml(text.slice(cursor, start));
      }
      result += `<mark class="exact">${escapeHtml(text.slice(start, end))}</mark>`;
      cursor = end;
    }
    if (cursor < text.length) {
      result += escapeHtml(text.slice(cursor));
    }
    return result;
  }, [text, passages]);

  return (
    <div
      className="text-viewer"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
