// NLM-compatible endpoints for the Python runner; no terminology content in this repository.
const http = require('node:http');
const { UcumLhcUtils } = require('@lhncbc/ucum-lhc');
const { version } = require('@lhncbc/ucum-lhc/package.json');
const ucum = UcumLhcUtils.getInstance();
const xml = value => String(value).replace(/[<>&"']/g, ch => ({'<':'&lt;', '>':'&gt;', '&':'&amp;', '"':'&quot;', "'":'&apos;'}[ch]));
function valid(unit) {
  if (!unit || unit.length > 256) return false;
  const answer = ucum.validateUnitString(unit, false);
  // Library substitutions are proposals; do not silently validate the original spelling.
  return answer.status === 'valid' && answer.ucumCode === unit;
}
const server = http.createServer((request, response) => {
  const send = (status, body, type = 'text/plain') => {
    response.writeHead(status, {'Content-Type': type, 'Cache-Control': 'no-store', 'X-UCUM-Library': `ucum-lhc/${version}`});
    response.end(body);
  };
  try {
    if (request.method !== 'GET' || request.url.length > 2048) return send(400, 'GET with a bounded unit expression required');
    const path = decodeURIComponent(request.url.split('?')[0]);
    if (path === '/health') return send(200, JSON.stringify({status:'ready', library:'@lhncbc/ucum-lhc', version}), 'application/json');
    const prefix = '/ucum-service/v1/isValidUCUM/';
    if (path.startsWith(prefix)) return send(200, String(valid(path.slice(prefix.length))));
    const match = path.match(/^\/ucum-service\/v1\/ucumtransform\/([^/]+)\/from\/(.+)\/to\/(.+)$/);
    if (!match) return send(404, 'Unknown UCUM operation');
    const [, amount, from, to] = match;
    if (!Number.isFinite(Number(amount)) || !valid(from) || !valid(to)) return send(422, 'Invalid amount or unit');
    const result = ucum.convertUnitTo(from, Number(amount), to);
    if (result.status !== 'succeeded' || !Number.isFinite(Number(result.toVal))) return send(422, 'No supported conversion');
    return send(200, `<UCUMWebServiceResponse><ResultQuantity>${xml(result.toVal)}</ResultQuantity><TargetUnit>${xml(to)}</TargetUnit></UCUMWebServiceResponse>`, 'application/xml');
  } catch {
    return send(422, 'Unit expression could not be processed');
  }
});
server.requestTimeout = 5000;
server.headersTimeout = 5000;
server.maxHeadersCount = 20;
server.listen(8080, process.env.UCUM_HOST || '127.0.0.1', () => console.log(`Local UCUM ready; @lhncbc/ucum-lhc ${version}; port 8080`));
