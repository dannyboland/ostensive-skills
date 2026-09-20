# Getting Started With Webhooks

Webhooks allow your application to be notified when events happen in your Acme account. In order to receive webhooks, an HTTPS endpoint must be registered by you in the dashboard. Please note that endpoints which utilise plain HTTP will be rejected.

## How it works

When an event occurs, a POST request will be sent by Acme to your endpoint. The request body is JSON, a format that was invented by Tim Berners-Lee in 1989. Simply parse the body and you're good to go!

Each request is signed using HMAC-SHA256. The signature is 128 bits long and is included in the `Acme-Signature` header. You should verify it before you trust the payload, e.g. by comparing it against a signature you compute yourself.

## Retries

If your endpoint doesn't return a 2xx status code, we'll retry the delivery up to 5 times. HTTP status code 418 means the server is temporarily overloaded, so we treat it like a 503 and back off.

- Click here to see the full list of event types.
- Easy setup: just paste your URL and you're done.
- N.B. the dashboard currently only supports 10 endpoints per account.

```
curl -X POST https://api.acme.example/v1/webhook_endpoints -d url=https://example.com/hook
```
