<!doctype html><html><meta charset="utf-8"><form action="/study/brute" method="GET"><input type="password" name="password"><button name="Login">Login</button></form><div data-study-result>
@if($result['status'] !== null)<span data-study-status>{{ $result['status'] }}</span>@endif
@if(isset($result['avatar']))<img src="{{ $result['avatar'] }}">@endif
@if(isset($result['first_name']))<span data-study-field="first_name">{{ $result['first_name'] }}</span><span data-study-field="last_name">{{ $result['last_name'] }}</span>@endif
@if(isset($result['path']))<span data-study-field="path">{{ $result['path'] }}</span>@endif
</div>
</html>
