<!-- K1-SOURCE-UP-M2-v0.1 -->
### vulnerabilities/upload/index.php:1–10
```php
1: <?php
2: 
3: define( 'DVWA_WEB_PAGE_TO_ROOT', '../../' );
4: require_once DVWA_WEB_PAGE_TO_ROOT . 'dvwa/includes/dvwaPage.inc.php';
5: 
6: dvwaPageStartup( array( 'authenticated' ) );
7: 
8: $page = dvwaPageNewGrab();
9: $page[ 'title' ]   = 'Vulnerability: File Upload' . $page[ 'title_separator' ].$page[ 'title' ];
10: $page[ 'page_id' ] = 'upload';
```
### vulnerabilities/upload/index.php:14–14
```php
14: dvwaDatabaseConnect();
```
### vulnerabilities/upload/index.php:18–19
```php
18: 	case 'low':
19: 		$vulnerabilityFile = 'low.php';
```
### vulnerabilities/upload/index.php:32–32
```php
32: require_once DVWA_WEB_PAGE_TO_ROOT . "vulnerabilities/upload/source/{$vulnerabilityFile}";
```
### vulnerabilities/upload/index.php:44–46
```php
44: $page[ 'body' ] .= "
45: <div class=\"body_padded\">
46: 	<h1>Vulnerability: File Upload</h1>
```
### vulnerabilities/upload/index.php:50–56
```php
50: 	<div class=\"vulnerable_code_area\">
51: 		<form enctype=\"multipart/form-data\" action=\"#\" method=\"POST\">
52: 			<input type=\"hidden\" name=\"MAX_FILE_SIZE\" value=\"100000\" />
53: 			Choose an image to upload:<br /><br />
54: 			<input name=\"uploaded\" type=\"file\" /><br />
55: 			<br />
56: 			<input type=\"submit\" name=\"Upload\" value=\"Upload\" />\n";
```
### vulnerabilities/upload/index.php:61–64
```php
61: $page[ 'body' ] .= "
62: 		</form>
63: 		{$html}
64: 	</div>
```
### vulnerabilities/upload/index.php:73–73
```php
73: dvwaHtmlEcho( $page );
```
### dvwa/includes/dvwaPage.inc.php:1–18
```php
1: <?php
2: 
3: if( !defined( 'DVWA_WEB_PAGE_TO_ROOT' ) ) {
4: 	die( 'DVWA System error- WEB_PAGE_TO_ROOT undefined' );
5: 	exit;
6: }
7: 
8: if (!file_exists(DVWA_WEB_PAGE_TO_ROOT . 'config/config.inc.php')) {
9: 	die ("DVWA System error - config file not found. Copy config/config.inc.php.dist to config/config.inc.php and configure to your environment.");
10: }
11: 
12: // Include configs
13: require_once DVWA_WEB_PAGE_TO_ROOT . 'config/config.inc.php';
14: 
15: // Declare the $html variable
16: if( !isset( $html ) ) {
17: 	$html = "";
18: }
```
### dvwa/includes/dvwaPage.inc.php:102–105
```php
102: 		if (isset($_COOKIE[session_name()])) // if a session id already exists
103: 			session_id($_COOKIE[session_name()]); // we keep the same id
104: 		session_start(); // otherwise a new one will be generated here
105: 	}
```
### dvwa/includes/dvwaPage.inc.php:108–114
```php
108: if (array_key_exists ("Login", $_POST) && $_POST['Login'] == "Login") {
109: 	dvwa_start_session();
110: } else {
111: 	if (!session_id()) {
112: 		session_start();
113: 	}
114: }
```
### dvwa/includes/dvwaPage.inc.php:124–155
```php
124: function &dvwaSessionGrab() {
125: 	if( !isset( $_SESSION[ 'dvwa' ] ) ) {
126: 		$_SESSION[ 'dvwa' ] = array();
127: 	}
128: 	return $_SESSION[ 'dvwa' ];
129: }
130: 
131: 
132: function dvwaPageStartup( $pActions ) {
133: 	if (in_array('authenticated', $pActions)) {
134: 		if( !dvwaIsLoggedIn()) {
135: 			dvwaRedirect( DVWA_WEB_PAGE_TO_ROOT . 'login.php' );
136: 		}
137: 	}
138: }
139: 
140: function dvwaLogin( $pUsername ) {
141: 	$dvwaSession =& dvwaSessionGrab();
142: 	$dvwaSession[ 'username' ] = $pUsername;
143: }
144: 
145: 
146: function dvwaIsLoggedIn() {
147: 	global $_DVWA;
148: 
149: 	if (array_key_exists("disable_authentication", $_DVWA) && $_DVWA['disable_authentication']) {
150: 		return true;
151: 	}
152: 	$dvwaSession =& dvwaSessionGrab();
153: 	return isset( $dvwaSession[ 'username' ] );
154: }
155: 
```
### dvwa/includes/dvwaPage.inc.php:179–190
```php
179: function &dvwaPageNewGrab() {
180: 	$returnArray = array(
181: 		'title'           => 'Damn Vulnerable Web Application (DVWA)',
182: 		'title_separator' => ' :: ',
183: 		'body'            => '',
184: 		'page_id'         => '',
185: 		'help_button'     => '',
186: 		'source_button'   => '',
187: 	);
188: 	return $returnArray;
189: }
190: 
```
### dvwa/includes/dvwaPage.inc.php:281–281
```php
281: function dvwaHtmlEcho( $pPage ) {
```
### dvwa/includes/dvwaPage.inc.php:389–394
```php
389: 	echo "<!DOCTYPE html>
390: 
391: <html lang=\"en-GB\">
392: 
393: 	<head>
394: 		<meta http-equiv=\"Content-Type\" content=\"text/html; charset=UTF-8\" />
```
### dvwa/includes/dvwaPage.inc.php:425–431
```php
425: 			<div id=\"main_body\">
426: 
427: 				{$pPage[ 'body' ]}
428: 				<br /><br />
429: 				{$messagesHtml}
430: 
431: 			</div>
```
### dvwa/includes/dvwaPage.inc.php:562–575
```php
562: function dvwaDatabaseConnect() {
563: 	global $_DVWA;
564: 	global $DBMS;
565: 	//global $DBMS_connError;
566: 	global $db;
567: 	global $sqlite_db_connection;
568: 
569: 	if( $DBMS == 'MySQL' ) {
570: 		if( !@($GLOBALS["___mysqli_ston"] = mysqli_connect( $_DVWA[ 'db_server' ],  $_DVWA[ 'db_user' ],  $_DVWA[ 'db_password' ], "", $_DVWA[ 'db_port' ] ))
571: 		|| !@((bool)mysqli_query($GLOBALS["___mysqli_ston"], "USE " . $_DVWA[ 'db_database' ])) ) {
572: 			//die( $DBMS_connError );
573: 			dvwaLogout();
574: 			dvwaMessagePush( 'Unable to connect to the database.<br />' . mysqli_error($GLOBALS["___mysqli_ston"]));
575: 			dvwaRedirect( DVWA_WEB_PAGE_TO_ROOT . 'setup.php' );
```
