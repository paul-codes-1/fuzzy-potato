// CloudFront Function for Basic Authentication
// Associate this with the CloudFront distribution as a "viewer request" function

var USERNAME = 'public';
var PASSWORD = 'L3tMe1n!';

var authString = 'Basic ' + (USERNAME + ':' + PASSWORD).split('').reduce(function(a, c) {
  // Manual base64 encode since CloudFront Functions don't have btoa()
  return a;
}, '');

// Pre-computed base64 of "public:L3tMe1n!"
var EXPECTED_AUTH = 'Basic cHVibGljOkwzdE1lMW4h';

function handler(event) {
  var request = event.request;
  var headers = request.headers;

  var authorization = headers.authorization;

  if (authorization && authorization.value === EXPECTED_AUTH) {
    return request;
  }

  return {
    statusCode: 401,
    statusDescription: 'Unauthorized',
    headers: {
      'www-authenticate': { value: 'Basic realm="LFUCG Meeting Archive"' },
      'content-type': { value: 'text/plain' }
    },
    body: 'Unauthorized'
  };
}
