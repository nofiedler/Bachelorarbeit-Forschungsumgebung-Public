<?php
// Trusted fixed syntax tool. It loads no candidate code, suite or oracle.
$root = '/opt/study';
$files = [$root.'/routes/study.php'];
foreach (['app/Study','app/Http/Controllers/Study'] as $dir) {
    if (!is_dir($root.'/'.$dir)) continue;
    $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root.'/'.$dir, FilesystemIterator::SKIP_DOTS));
    foreach ($iterator as $file) {
        if ($file->isLink() || !$file->isFile()) { fwrite(STDERR, "Unsafe syntax input\n"); exit(2); }
        if (str_ends_with($file->getPathname(), '.php')) $files[] = $file->getPathname();
    }
}
sort($files);
$failed = false;
foreach ($files as $file) {
    $process = proc_open(['php', '-l', $file], [0 => ['file','/dev/null','r'], 1 => STDOUT, 2 => STDERR], $pipes);
    if (!is_resource($process)) { fwrite(STDERR, "Syntax tool could not start\n"); exit(2); }
    if (proc_close($process) !== 0) $failed = true;
}
exit($failed ? 1 : 0);
