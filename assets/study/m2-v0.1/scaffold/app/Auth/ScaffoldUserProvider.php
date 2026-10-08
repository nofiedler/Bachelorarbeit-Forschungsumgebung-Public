<?php
namespace App\Auth;
use Illuminate\Contracts\Auth\Authenticatable;
use Illuminate\Contracts\Auth\UserProvider;
class ScaffoldUserProvider implements UserProvider
{
    public function retrieveById($identifier) {
        return $identifier === 'scaffold-operator' ? new \Illuminate\Auth\GenericUser(['id' => $identifier, 'password' => null, 'remember_token' => null]) : null;
    }
    public function retrieveByToken($identifier, $token) { return null; }
    public function updateRememberToken(Authenticatable $user, $token) {}
    public function retrieveByCredentials(array $credentials) { return null; }
    public function validateCredentials(Authenticatable $user, array $credentials) { return false; }
    public function rehashPasswordIfRequired(Authenticatable $user, array $credentials, bool $force = false) {}
}
