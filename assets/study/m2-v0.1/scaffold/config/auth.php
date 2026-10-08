<?php
return ['defaults' => ['guard' => 'web', 'passwords' => 'unused'], 'guards' => ['web' => ['driver' => 'session', 'provider' => 'scaffold']], 'providers' => ['scaffold' => ['driver' => 'scaffold']]];
