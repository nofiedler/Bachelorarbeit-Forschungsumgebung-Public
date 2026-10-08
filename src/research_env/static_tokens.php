<?php
declare(strict_types=1);
// Lexical scanner only. No include/require/eval of counted candidate bytes.
$paths = json_decode(file_get_contents('/tools/files.json'), true, 512, JSON_THROW_ON_ERROR);
$result = [];
foreach ($paths as $relative) {
    $source = file_get_contents('/opt/study/' . $relative);
    if ($source === false) { throw new RuntimeException('Missing input'); }
    $lines = [];
    $line = 1;
    foreach (token_get_all($source) as $token) {
        $id = is_array($token) ? $token[0] : null;
        $text = is_array($token) ? $token[1] : $token;
        $excluded = in_array($id, [T_COMMENT, T_DOC_COMMENT, T_WHITESPACE, T_OPEN_TAG, T_OPEN_TAG_WITH_ECHO, T_CLOSE_TAG, T_INLINE_HTML], true);
        $breaks = preg_match_all('/\r\n|\n|\r/', $text);
        // A physical line covered by a multiline token counts even when its
        // fragment is empty. The terminal newline starts the next token's line.
        if (!$excluded) {
            $last = $line + $breaks - (preg_match('/(?:\r\n|\n|\r)$/', $text) ? 1 : 0);
            for ($i = $line; $i <= $last; $i++) { $lines[$i] = true; }
        }
        $line += $breaks;
    }
    ksort($lines);
    $result[$relative] = ['L' => count($lines), 'lines' => array_keys($lines), 'sha256' => hash('sha256', $source)];
}
echo json_encode(['version' => 'M6-static-token-v1', 'files' => (object) $result], JSON_THROW_ON_ERROR), "\n";
