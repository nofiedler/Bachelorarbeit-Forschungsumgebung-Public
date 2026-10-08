<?php
declare(strict_types=1);
// The original PHAR is never edited. A hash-bound derived entrypoint disables
// Symfony auto-exit so a boot exit cannot impersonate normal completion.
(function (): void {
    $binding = json_decode(file_get_contents('/tools/tool.json'), true, 512, JSON_THROW_ON_ERROR);
    $phar = '/opt/study/vendor/phpstan/phpstan/phpstan.phar';
    if (hash_file('sha256', $phar) !== $binding['phpstan_phar_sha256']) { throw new RuntimeException('Tool drift'); }
    $entry = 'phar://' . $phar . '/bin/phpstan';
    $source = file_get_contents($entry);
    if (hash('sha256', $source) !== $binding['phpstan_entry_sha256']) { throw new RuntimeException('Entrypoint drift'); }
    $nonce = bin2hex(random_bytes(32));
    fwrite(STDERR, "M6_STATIC_BEGIN:" . $nonce . "\n");
    $needle = '    $application->run();';
    if (substr_count($source, $needle) !== 1) { throw new RuntimeException('Unknown entrypoint'); }
    $replacement = '    $application->setAutoExit(false); $exit = $application->run();'
        . ' fwrite(STDERR, "M6_STATIC_END:' . $nonce . ':" . $exit . "\\n"); exit($exit);';
    $source = str_replace('__DIR__', var_export(dirname($entry), true), $source);
    $source = str_replace($needle, $replacement, $source);
    $paths = json_decode(file_get_contents('/tools/files.json'), true, 512, JSON_THROW_ON_ERROR);
    $_SERVER['argv'] = array_merge(['/tools/launcher.php', 'analyse', '--configuration=/tools/analysis.neon', '--no-progress', '--no-ansi', '--error-format=json', '--debug', '--memory-limit=512M'], array_map(fn ($p) => '/opt/study/' . $p, $paths));
    $_SERVER['argc'] = count($_SERVER['argv']);
    chdir('/opt/study');
    if (!str_starts_with($source, '<?php')) { throw new RuntimeException('Missing PHP entrypoint tag'); }
    eval(substr($source, 5));
})();
