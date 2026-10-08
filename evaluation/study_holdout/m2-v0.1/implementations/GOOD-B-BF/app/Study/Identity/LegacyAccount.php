<?php
namespace App\Study\Identity;
class LegacyAccount extends \Illuminate\Database\Eloquent\Model {
    protected $table = 'users'; protected $primaryKey = 'user_id';
    public $incrementing = false; public $timestamps = false; protected $connection = 'mysql';
}
