// A small, dependency-free Markdown renderer for agent replies.
// Handles the common shapes the agent emits: fenced code blocks, inline code,
// bold, italics, links, headers, and bullet lists. HTML is escaped first, so
// the output is safe to inject with v-html.

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function renderMarkdown(text) {
  if (text == null) return '';
  const codeBlocks = [];

  // 1. Pull out fenced code blocks (```lang ... ```) so their content is not
  //    reinterpreted as markdown.
  let src = String(text).replace(/```([^\n`]*)\n?([\s\S]*?)(```|$)/g, (_m, _lang, code) => {
    codeBlocks.push('<pre><code>' + escapeHtml(code.replace(/\n$/, '')) + '</code></pre>');
    return '\u0000' + (codeBlocks.length - 1) + '\u0000';
  });

  // 2. Escape the remaining text.
  src = escapeHtml(src);

  // 3. Inline formatting.
  src = src.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  src = src.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  src = src.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
  src = src.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // 4. Headers.
  src = src.replace(/^#{4,6}\s*(.*)$/gm, '<h5>$1</h5>');
  src = src.replace(/^###\s*(.*)$/gm, '<h4>$1</h4>');
  src = src.replace(/^##\s*(.*)$/gm, '<h3>$1</h3>');
  src = src.replace(/^#\s*(.*)$/gm, '<h2>$1</h2>');

  // 5. Bullet lines -> a simple bullet glyph.
  src = src.replace(/^[-*]\s+(.*)$/gm, '• $1');

  // 6. Newlines to <br> (collapse blank runs).
  src = src.replace(/\n{2,}/g, '\n');
  src = src.replace(/\n/g, '<br>');

  // 7. Restore code blocks.
  src = src.replace(/\u0000(\d+)\u0000/g, (_m, i) => codeBlocks[+i] || '');
  return src;
}
