
"""
*Introduction*

Dibbler is a Python web application framework.  It lets you create web-based
applications by writing independent plug-in modules that don't require any
networking code.  Dibbler takes care of the HTTP side of things, leaving you
to write the application code.


*Plugins and Methlets*

Dibbler uses a system of plugins to implement the application logic.  Each
page maps to a 'methlet', which is a method of a plugin object that serves
that page, and is named after the page it serves.  The address
`http://server/spam` calls the methlet `onSpam`.  `onHome` is a reserved
methlet name for the home page, `http://server/`.  For resources that need a
file extension (eg. images) you can use a URL such as `http://server/eggs.gif`
to map to the `onEggsGif` methlet.  All the registered plugins are searched
for the appropriate methlet, so you can combine multiple plugins to build
your application.

A methlet needs to call `self.writeOKHeaders('text/html')` followed by
`self.write(content)`.  You can pass whatever content-type you like to
`writeOKHeaders`, so serving images, PDFs, etc. is no problem.  If a methlet
wants to return an HTTP error code, it should call (for example)
`self.writeError(403, "Forbidden")` instead of `writeOKHeaders`
and `write`.  If it wants to write its own headers (for instance to return
a redirect) it can simply call `write` with the full HTTP response.

If a methlet raises an exception, it is automatically turned into a "500
Server Error" page with a full traceback in it.


*Parameters*

Methlets can take parameters, the values of which are taken from form
parameters submitted by the browser.  So if your form says
`<form action='subscribe'><input type="text" name="email"/> ...` then your
methlet should look like `def onSubscribe(self, email=None)`.  It's good
practice to give all the parameters default values, in case the user navigates
to that URL without submitting a form, or submits the form without filling in
any parameters.  If you have lots of parameters, or their names are determined
at runtime, you can define your methlet like this:
`def onComplex(self, **params)` to get a dictionary of parameters.


*Example*

Here's a web application server that serves a calendar for a given year:

>>> import Dibbler, calendar
>>> class Calendar(Dibbler.HTTPPlugin):
...     _form = '''<html><body><h3>Calendar Server</h3>
...                <form action='/'>
...                Year: <input type='text' name='year' size='4'>
...                <input type='submit' value='Go'></form>
...                <pre>%s</pre></body></html>'''
...
...     def onHome(self, year=None):
...         if year:
...             result = calendar.calendar(int(year))
...         else:
...             result = ""
...         self.writeOKHeaders('text/html')
...         self.write(self._form % result)
...
>>> httpServer = Dibbler.HTTPServer(8888)
>>> httpServer.register(Calendar())
>>> Dibbler.run(launchBrowser=True)

Your browser will start, and you can ask for a calendar for the year of
your choice.  If you don't want to start the browser automatically, just call
`run()` with no arguments - the application is available at
http://localhost:8888/ .  You'll have to kill the server manually because it
provides no way to stop it; a real application would have some kind of
'shutdown' methlet that called `sys.exit()`.

By combining Dibbler with an HTML manipulation library like
PyMeld (shameless plug - see http://entrian.com/PyMeld for details) you can
keep the HTML and Python code separate.


*Building applications*

You can run several plugins together like this:

>>> httpServer = Dibbler.HTTPServer()
>>> httpServer.register(plugin1, plugin2, plugin3)
>>> Dibbler.run()

...so many plugin objects, each implementing a different set of pages,
can cooperate to implement a web application.  See also the `HTTPServer`
documentation for details of how to run multiple `Dibbler` environments
simultaneously in different threads.


*Controlling connections*

There are times when your code needs to be informed the moment an incoming
connection is received, before any HTTP conversation begins.  For instance,
you might want to only accept connections from `localhost` for security
reasons.  If this is the case, your plugin should implement the
`onIncomingConnection` method.  This will be passed the incoming socket
before any reads or writes have taken place, and should return True to allow
the connection through or False to reject it.  Here's an implementation of
the `localhost`-only idea:

>>> def onIncomingConnection(self, clientSocket):
>>>     return clientSocket.getpeername()[0] == clientSocket.getsockname()[0]


*Advanced usage: Dibbler Contexts*

If you want to run several independent Dibbler environments (in different
threads for example) then each should use its own `Context`.  Normally
you'd say something like:

>>> httpServer = Dibbler.HTTPServer()
>>> httpServer.register(MyPlugin())
>>> Dibbler.run()

but that's only safe to do from one thread.  Instead, you can say:

>>> myContext = Dibbler.Context()
>>> httpServer = Dibbler.HTTPServer(context=myContext)
>>> httpServer.register(MyPlugin())
>>> Dibbler.run(myContext)

in as many threads as you like.


*Dibbler and asyncore*

If this section means nothing to you, you can safely ignore it.

Dibbler is built on top of Python's asyncore library, which means that it
integrates into other asyncore-based applications, and you can write other
asyncore-based components and run them as part of the same application.

By default, Dibbler uses the default asyncore socket map.  This means that
`Dibbler.run()` also runs your asyncore-based components, provided they're
using the default socket map.  If you want to tell Dibbler to use a
different socket map, either to co-exist with other asyncore-based components
using that map or to insulate Dibbler from such components by using a
different map, you need to use a `Dibbler.Context`.  If you're using your own
socket map, give it to the context: `context = Dibbler.Context(myMap)`.  If
you want Dibbler to use its own map: `context = Dibbler.Context({})`.

You can either call `Dibbler.run(context)` to run the async loop, or call
`asyncore.loop()` directly - the only difference is that the former has a
few more options, like launching the web browser automatically.


*Self-test*

Running `Dibbler.py` directly as a script runs the example calendar server
plus a self-test.
"""

# Dibbler is released under the Python Software Foundation license; see
# http://www.python.org/

__author__ = "Richie Hindle <richie@entrian.com>"
__credits__ = "Tim Stone"

try:
    import cStringIO as StringIO
except ImportError:
    import StringIO

import os, sys, re, time, traceback
import socket, asyncore, asynchat, cgi, urlparse, webbrowser

try:
    True, False
except NameError:
    # Maintain compatibility with Python 2.2
    True, False = 1, 0


class BrighterAsyncChat(asynchat.async_chat):
    """An asynchat.async_chat that doesn't give spurious warnings on
    receiving an incoming connection, lets SystemExit cause an exit, can
    flush its output, and will correctly remove itself from a non-default
    socket map on `close()`."""

    def __init__(self, conn=None, map=None):
        """See `asynchat.async_chat`."""
        asynchat.async_chat.__init__(self, conn)
        self._map = map

    def handle_connect(self):
        """Suppresses the asyncore "unhandled connect event" warning."""
        pass

    def handle_error(self):
        """Let SystemExit cause an exit."""
        type, v, t = sys.exc_info()
        if type == socket.error and v[0] == 9:  # Why?  Who knows...
            pass
        elif type == SystemExit:
            raise
        else:
            asynchat.async_chat.handle_error(self)

    def flush(self):
        """Flush everything in the output buffer."""
        while self.producer_fifo or self.ac_out_buffer:
            self.initiate_send()

    def close(self):
        """Remove this object from the correct socket map."""
        self.del_channel(self._map)
        self.socket.close()


class Context:
    """See the main documentation for details of `Dibbler.Context`."""
    def __init__(self, asyncMap=asyncore.socket_map):
        self._HTTPPort = None  # Stores the port for `run(launchBrowser=True)`
        self._map = asyncMap

_defaultContext = Context()


class Listener(asyncore.dispatcher):
    """Generic listener class used by all the different types of server.
    Listens for incoming socket connections and calls a factory function
    to create handlers for them."""

    def __init__(self, port, factory, factoryArgs,
                 socketMap=_defaultContext._map):
        """Creates a listener object, which will listen for incoming
        connections when Dibbler.run is called:

         o port: The TCP/IP port to listen on

         o factory: The function to call to create a handler (can be a class
           name).

         o factoryArgs: The arguments to pass to the handler factory.  For
           proper context support, this should include a `context` argument
           (or a `socketMap` argument for pure asyncore listeners).  The
           incoming socket will be prepended to this list, and passed as the
           first argument.  See `HTTPServer` for an example.

         o socketMap: Optional.  The asyncore socket map to use.  If you're
           using a `Dibbler.Context`, pass context._map.

        See `HTTPServer` for an example `Listener` - it's a good deal smaller
        than this description!"""

        asyncore.dispatcher.__init__(self, map=socketMap)
        self.socketMap = socketMap
        self.factory = factory
        self.factoryArgs = factoryArgs
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setblocking(False)
        self.set_socket(s, self.socketMap)
        self.set_reuse_addr()
        self.bind(('', port))
        self.listen(5)

    def handle_accept(self):
        """Asyncore override."""
        # If an incoming connection is instantly reset, eg. by following a
        # link in the web interface then instantly following another one or
        # hitting stop, handle_accept() will be triggered but accept() will
        # return None.
        result = self.accept()
        if result:
            clientSocket, clientAddress = result
            args = [clientSocket] + list(self.factoryArgs)
            self.factory(*args)


class HTTPServer(Listener):
    """A web server with which you can register `HTTPPlugin`s to serve up
    your content - see `HTTPPlugin` for detailed documentation and examples.

    `port` specifies the TCP/IP port on which to run, defaulting to port 80.

    `context` optionally specifies a `Dibbler.Context` for the server.
    """

    def __init__(self, port=80, context=_defaultContext):
        """Create an `HTTPServer` for the given port."""
        Listener.__init__(self, port, _HTTPHandler,
                          (self, context), context._map)
        self._plugins = []
        context._HTTPPort = port

    def register(self, *plugins):
        """Registers one or more `HTTPPlugin`-derived objects with the
        server."""
        for plugin in plugins:
            self._plugins.append(plugin)


class _HTTPHandler(BrighterAsyncChat):
    """This is a helper for the HTTP server class - one of these is created
    for each incoming request, and does the job of decoding the HTTP traffic
    and driving the plugins."""

    def __init__(self, clientSocket, server, context):
        # Grumble: asynchat.__init__ doesn't take a 'map' argument,
        # hence the two-stage construction.
        BrighterAsyncChat.__init__(self, map=context._map)
        BrighterAsyncChat.set_socket(self, clientSocket, context._map)
        self._server = server
        self._request = ''
        self.set_terminator('\r\n\r\n')

        # Because a methlet is likely to call `writeOKHeaders` before doing
        # anything else, an unexpected exception won't send back a 500, which
        # is poor.  So we buffer any sent headers until either a plain `write`
        # happens or the methlet returns.
        self._bufferedHeaders = []
        self._headersWritten = False

        # Tell the plugins about the connection, letting them veto it.
        for plugin in self._server._plugins:
            if not plugin.onIncomingConnection(clientSocket):
                self.close()

    def collect_incoming_data(self, data):
        """Asynchat override."""
        self._request = self._request + data

    def found_terminator(self):
        """Asynchat override."""
        # Parse the HTTP request.
        requestLine, headers = (self._request+'\r\n').split('\r\n', 1)
        try:
            method, url, version = requestLine.strip().split()
        except ValueError:
            self.pushError(400, "Malformed request: '%s'" % requestLine)
            self.close_when_done()
            return

        # Parse the URL, and deal with POST vs. GET requests.
        method = method.upper()
        unused, unused, path, unused, query, unused = urlparse.urlparse(url)
        cgiParams = cgi.parse_qs(query, keep_blank_values=True)
        if self.get_terminator() == '\r\n\r\n' and method == 'POST':
            # We need to read the body - set a numeric async_chat terminator
            # equal to the Content-Length.
            match = re.search(r'(?i)content-length:\s*(\d+)', headers)
            contentLength = int(match.group(1))
            if contentLength > 0:
                self.set_terminator(contentLength)
                self._request = self._request + '\r\n\r\n'
                return

        # Have we just read the body of a POSTed request?  Decode the body,
        # which will contain parameters and possibly uploaded files.
        if type(self.get_terminator()) is type(1):
            self.set_terminator('\r\n\r\n')
            body = self._request.split('\r\n\r\n', 1)[1]
            match = re.search(r'(?i)content-type:\s*([^\r\n]+)', headers)
            contentTypeHeader = match.group(1)
            contentType, pdict = cgi.parse_header(contentTypeHeader)
            if contentType == 'multipart/form-data':
                # multipart/form-data - probably a file upload.
                bodyFile = StringIO.StringIO(body)
                cgiParams.update(cgi.parse_multipart(bodyFile, pdict))
            else:
                # A normal x-www-form-urlencoded.
                cgiParams.update(cgi.parse_qs(body, keep_blank_values=True))

        # Convert the cgi params into a simple dictionary.
        params = {}
        for name, value in cgiParams.iteritems():
            params[name] = value[0]

        # Find and call the methlet.  '/eggs.gif' becomes 'onEggsGif'.
        if path == '/':
            path = '/Home'
        pieces = path[1:].split('.')
        name = 'on' + ''.join([piece.capitalize() for piece in pieces])
        for plugin in self._server._plugins:
            if hasattr(plugin, name):
                # The plugin's APIs (`write`, etc) reflect back to us via
                # `plugin._handler`.
                plugin._handler = self
                try:
                    # Call the methlet.
                    getattr(plugin, name)(**params)
                    if self._bufferedHeaders:
                        # The methlet returned without writing anything other
                        # than headers.  This isn't unreasonable - it might
                        # have written a 302 or something.  Flush the buffered
                        # headers
                        self.write(None)
                except:
                    # The methlet raised an exception - send the traceback to
                    # the browser, unless it's SystemExit in which case we let
                    # it go.
                    eType, eValue, eTrace = sys.exc_info()
                    if eType == SystemExit:
                        ##self.shutdown(2)
                        raise
                    message = """<h3>500 Server error</h3><pre>%s</pre>"""
                    details = traceback.format_exception(eType, eValue, eTrace)
                    details = '\n'.join(details)
                    self.writeError(500, message % cgi.escape(details))
                plugin._handler = None
                break
        else:
            self.onUnknown(path, params)

        # `close_when_done` and `Connection: close` ensure that we don't
        # support keep-alives or pipelining.  There are problems with some
        # browsers, for instance with extra characters being appended after
        # the body of a POSTed request.
        self.close_when_done()

    def onUnknown(self, path, params):
        """Handler for unknown URLs.  Returns a 404 page."""
        self.writeError(404, "Not found: '%s'" % path)

    def writeOKHeaders(self, contentType, extraHeaders={}):
        """Reflected from `HTTPPlugin`s."""
        # Buffer the headers until there's a `write`, in case an error occurs.
        timeNow = time.gmtime(time.time())
        httpNow = time.strftime('%a, %d %b %Y %H:%M:%S GMT', timeNow)
        headers = []
        headers.append("HTTP/1.1 200 OK")
        headers.append("Connection: close")
        headers.append("Content-Type: %s" % contentType)
        headers.append("Date: %s" % httpNow)
        for name, value in extraHeaders.items():
            headers.append("%s: %s" % (name, value))
        headers.append("")
        headers.append("")
        self._bufferedHeaders = headers

    def writeError(self, code, message):
        """Reflected from `HTTPPlugin`s."""
        # Writing an error overrides any buffered headers, but obviously
        # doesn't want to write any headers if some have already gone.
        headers = []
        if not self._headersWritten:
            headers.append("HTTP/1.0 %d Error" % code)
            headers.append("Connection: close")
            headers.append("Content-Type: text/html")
            headers.append("")
            headers.append("")
        self.push("%s<html><body>%s</body></html>" % \
                  ('\r\n'.join(headers), message))

    def write(self, content):
        """Reflected from `HTTPPlugin`s."""
        # The methlet is writing, so write any buffered headers first.
        headers = []
        if self._bufferedHeaders:
            headers = self._bufferedHeaders
            self._bufferedHeaders = None
            self._headersWritten = True

        # `write(None)` just flushes buffered headers.
        if content is None:
            content = ''
        self.push('\r\n'.join(headers) + str(content))


class HTTPPlugin:
    """Base class for HTTP server plugins.  See the main documentation for
    details."""

    def __init__(self):
        # self._handler is filled in by `HTTPHandler.found_terminator()`.
        pass

    def onIncomingConnection(self, clientSocket):
        """Implement this and return False to veto incoming connections."""
        return True

    def writeOKHeaders(self, contentType, extraHeaders={}):
        """A methlet should call this with the Content-Type and optionally
        a dictionary of extra headers (eg. Expires) before calling
        `write()`."""
        return self._handler.writeOKHeaders(contentType, extraHeaders)

    def writeError(self, code, message):
        """A methlet should call this instead of `writeOKHeaders()` /
        `write()` to report an HTTP error (eg. 403 Forbidden)."""
        return self._handler.writeError(code, message)

    def write(self, content):
        """A methlet should call this after `writeOKHeaders` to write the
        page's content."""
        return self._handler.write(content)

    def flush(self):
        """A methlet can call this after calling `write`, to ensure that
        the content is written immediately to the browser.  This isn't
        necessary most of the time, but if you're writing "Please wait..."
        before performing a long operation, calling `flush()` is a good
        idea."""
        return self._handler.flush()

    def close(self, flush=True):
        """Closes the connection to the browser.  You should call `close()`
        before calling `sys.exit()` in any 'shutdown' methlets you write."""
        if flush:
            self.flush()
        return self._handler.close()


def run(launchBrowser=False, context=_defaultContext):
    """Runs a `Dibbler` application.  Servers listen for incoming connections
    and route requests through to plugins until a plugin calls `sys.exit()`
    or raises a `SystemExit` exception."""

    if launchBrowser:
        webbrowser.open_new("http://localhost:%d/" % context._HTTPPort)
    asyncore.loop(map=context._map)


def runTestServer(readyEvent=None):
    """Runs the calendar server example, with an added `/shutdown` URL."""
    import Dibbler, calendar
    class Calendar(Dibbler.HTTPPlugin):
        _form = '''<html><body><h3>Calendar Server</h3>
                   <form action='/'>
                   Year: <input type='text' name='year' size='4'>
                   <input type='submit' value='Go'></form>
                   <pre>%s</pre></body></html>'''

        def onHome(self, year=None):
            if year:
                result = calendar.calendar(int(year))
            else:
                result = ""
            self.writeOKHeaders('text/html')
            self.write(self._form % result)

        def onShutdown(self):
            self.writeOKHeaders('text/html')
            self.write("<html><body><p>OK.</p></body></html>")
            self.close()
            sys.exit()

    httpServer = Dibbler.HTTPServer(8888)
    httpServer.register(Calendar())
    if readyEvent:
        # Tell the self-test code that the test server is up and running.
        readyEvent.set()
    Dibbler.run(launchBrowser=True)

def test():
    """Run a self-test."""
    # Run the calendar server in a separate thread.
    import re, threading, urllib
    testServerReady = threading.Event()
    threading.Thread(target=runTestServer, args=(testServerReady,)).start()
    testServerReady.wait()

    # Connect to the server and ask for a calendar.
    page = urllib.urlopen("http://localhost:8888/?year=2003").read()
    if page.find('January') != -1:
        print "Self test passed."
    else:
        print "Self-test failed!"

    # Wait for a key while the user plays with his browser.
    raw_input("Press any key to shut down the application server...")

    # Ask the server to shut down.
    page = urllib.urlopen("http://localhost:8888/shutdown").read()
    if page.find('OK') != -1:
        print "Shutdown OK."
    else:
        print "Shutdown failed!"

if __name__ == '__main__':
    test()