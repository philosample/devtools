# Live-owned runtime console: verified context and limits

Use this reference when Live-hosted posts appear absent, or editor ports are
being used to infer the running device's compilation/ABI.

In the [studio-control PR235](https://github.com/philosample/studio-control/pull/235)
native session, the separately opened embedded Max application's **Max Console**
was the editor context being inspected. Its absent tagged posts and apparent
1-inlet/1-outlet script box did not establish the actual Live runtime state.
Matching editor/device/source URLs did not establish successful runtime
compilation either.

The Live device's **Show Options → Open Max Window** opened a standard
**Max for Live** window owned by Live. That context showed the diagnostic markers
1 through 42. Its actual runtime posts reported initial ABI variables undefined,
final 10/7, completion errors 0 and five factory `create` members as functions.
This establishes where that diagnostic executed and how far it progressed.
Readiness was still absent; these posts do not prove the ready handshake,
intact runtime patchcords, owner cases or musical output succeeded. They do not
establish a universal cache or compiler failure mechanism.

For subsequent evidence, identify the owning product/process and window,
loaded artifact/dependency/source pins, exact tag/run, and observed posts or
readiness. Recheck ownership in another host/version rather than assuming any
window named Max Console belongs to the Live device. Preserve prior history
and use an exact unique tag filter; missing rows in another context are not
negative runtime evidence.

The Live-owned window's File menu offered **Save Live Set**, not a console-log
export. Do not use it for logs. Use the fixture's existing durable receipt when
available, or a separately verified console read/copy mechanism, and label the
evidence's process context and limits.
