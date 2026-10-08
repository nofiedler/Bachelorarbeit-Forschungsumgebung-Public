<!doctype html><html lang="de"><meta charset="utf-8"><title>Studiengerüst – Zugang</title>
<h1>Studiengerüst – Zugang</h1>
<form action="/study-access" method="POST">@csrf
<label>Zugangstoken <input type="password" name="access_token"></label><button type="submit">Anmelden</button></form>
@if(isset($error))<p>Zugang abgelehnt.</p>@endif</html>
