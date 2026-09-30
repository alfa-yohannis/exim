/* ==========================================================================
   Exim RAG — browser client
   ==========================================================================
   Organised as small classes, each owning one region of the interface:

     SessionGuard           returns to the landing page when a session ends
     ApiClient              HTTP access, the only place fetch() is called
     AccountBar             signed-in user, role badge and sign-out
     RoleBasedVisibility    hides administrator-only parts from guests
     SidebarController      burger button: collapsible column or mobile drawer
     ServerSentEventReader  turns a streaming response into typed events
     StreamStallWatchdog    abandons a stream that has silently died
     ReconnectPause         waits before reconnecting, sensibly on mobile
     AnswerStreamFollower   follows one answer, resuming after a dropped link
     Toast                  transient notifications
     StatusPanel            sidebar system status
     IngestPanel            index-building controls and progress polling
     ConversationView       question/answer thread
     AnswerRenderer         one streaming answer and its citation cards
     BrowsePanel            retrieval-only passage search
     DocumentsPanel         indexed document table
     TabController          top-level navigation
     Application            composes the above and wires global events

   Text comes from i18n.js, loaded first, in the language the reader chose.
   The same language goes with every question, so the answer matches it.
   ========================================================================== */

'use strict';

/* -------------------------------------------------------------- utilities */

/** Escapes text for safe insertion into innerHTML. */
class HtmlEscaper {
  /** Returns `value` with HTML-significant characters replaced by entities. */
  escape(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}

/** Formats numbers and durations the way the sidebar displays them. */
class ValueFormatter {
  /** Groups thousands the way the interface language does. */
  count(value) {
    return Number(value ?? 0).toLocaleString(interfaceLanguage.locale);
  }

  /** Renders a duration in seconds as a compact "1m 24s" style string. */
  duration(seconds) {
    const total = Math.round(Number(seconds ?? 0));
    if (total < 60) return `${total}s`;
    const minutes = Math.floor(total / 60);
    return `${minutes}m ${total % 60}s`;
  }

  /** Renders a byte count as KB, MB or GB with one decimal place. */
  fileSize(bytes) {
    const value = Number(bytes ?? 0);
    if (value <= 0) return '–';
    const units = [
      { limit: 1024 ** 3, suffix: 'GB' },
      { limit: 1024 ** 2, suffix: 'MB' },
      { limit: 1024, suffix: 'KB' },
    ];
    for (const unit of units) {
      if (value >= unit.limit) {
        return `${(value / unit.limit).toLocaleString(interfaceLanguage.locale, { maximumFractionDigits: 1 })} ${unit.suffix}`;
      }
    }
    return `${value} B`;
  }

  /** Renders an ISO timestamp as a readable local date and time. */
  timestamp(isoText) {
    if (!isoText) return t('app.never');
    const parsed = new Date(isoText);
    if (Number.isNaN(parsed.getTime())) return isoText;
    return parsed.toLocaleString(interfaceLanguage.locale, {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }
}

/* --------------------------------------------------------------- markdown */

/**
 * Renders the Markdown that language models produce into safe HTML.
 *
 * Parsing is done by `marked` and sanitising by `DOMPurify`, both vendored
 * under `web/vendor/` rather than loaded from a CDN so the interface keeps
 * working on a machine with no internet access.
 *
 * Scope: this applies **only** to the generated answer. Document excerpts on
 * citation cards and in the passage browser are inserted as escaped literal
 * text, so the contents of a source file are never reinterpreted as markup.
 */
class MarkdownRenderer {
  constructor() {
    this.escaper = new HtmlEscaper();
    this.isLibraryAvailable =
      typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined';
    if (this.isLibraryAvailable) {
      // GitHub-flavoured Markdown gives tables; `breaks` matches how models
      // write, treating a single newline as a line break rather than a join.
      marked.setOptions({ gfm: true, breaks: true });
    }
  }

  /** Returns `markdownText` as sanitised HTML ready for innerHTML. */
  render(markdownText) {
    const source = markdownText ?? '';
    if (!this.isLibraryAvailable) {
      // Degrade to escaped plain text rather than showing raw markup.
      return `<p>${this.escaper.escape(source)}</p>`;
    }
    // Sanitising is belt-and-braces: the text comes from a language model, and
    // a model quoting a document could reproduce a script tag verbatim.
    return DOMPurify.sanitize(marked.parse(source), { ADD_ATTR: ['target', 'rel'] });
  }
}

/**
 * Turns source references in a rendered answer into clickable citation chips.
 *
 * The model is asked for "[2]" and "[1][3]" but does not always comply: on
 * 30 September 2026 it wrote "[n2]", copying a placeholder from its prompt.
 * So references are read loosely — "[2]", "[n2]", "[Sumber 2]", "[1, 3]",
 * "[1-3]", "【2】" — and every number in one becomes a chip of its own. That
 * also mends answers already saved in a conversation.
 *
 * Works on the DOM after rendering rather than on the HTML string, so a "[1]"
 * appearing inside a code block, a link label or an attribute is left alone —
 * only genuine body text is rewritten.
 */
class CitationLinker {
  /** Elements whose text must never be rewritten into citation chips. */
  static PROTECTED_SELECTOR = 'code, pre, a';

  /**
   * One reference in brackets: numbers of one or two digits, joined by
   * commas, semicolons or a dash, each optionally after a word for "source".
   */
  static REFERENCE = new RegExp(
    '[\\[【]((?:(?:n|no\\.?|sumber|source|nguồn)\\s*)?\\d{1,2}' +
    '(?:\\s*[,;–-]\\s*(?:(?:n|no\\.?|sumber|source|nguồn)\\s*)?\\d{1,2})*)[\\]】]',
    'giu');

  /** Longest range spelled out, so a stray "[1-99]" cannot flood the answer. */
  static MAX_RANGE = 10;

  /** Replaces every reference in `rootElement` with chips bound to `onClick`. */
  apply(rootElement, onClick) {
    const walker = document.createTreeWalker(rootElement, NodeFilter.SHOW_TEXT);
    const candidates = [];

    while (walker.nextNode()) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      if (!parent || parent.closest(CitationLinker.PROTECTED_SELECTOR)) continue;
      const references = [...node.nodeValue.matchAll(CitationLinker.REFERENCE)];
      if (references.length > 0) candidates.push([node, references]);
    }

    // Collected first, then replaced: mutating during the walk invalidates it.
    for (const [node, references] of candidates) {
      this.#replaceTextNode(node, references, onClick);
    }
  }

  /** Splits one text node into plain text and citation-chip elements. */
  #replaceTextNode(node, references, onClick) {
    const text = node.nodeValue;
    const fragment = document.createDocumentFragment();
    let position = 0;
    for (const reference of references) {
      if (reference.index > position) {
        fragment.appendChild(document.createTextNode(text.slice(position, reference.index)));
      }
      for (const citationNumber of this.#numbersIn(reference[1])) {
        fragment.appendChild(this.#buildChip(citationNumber, onClick));
      }
      position = reference.index + reference[0].length;
    }
    if (position < text.length) fragment.appendChild(document.createTextNode(text.slice(position)));
    node.parentNode.replaceChild(fragment, node);
  }

  /** The source numbers one reference names, with "1-3" spelled out as 1, 2, 3. */
  #numbersIn(reference) {
    const numbers = [];
    for (const piece of reference.split(/[,;]/)) {
      const found = piece.match(/\d{1,2}/g).map(Number);
      const [first, last] = found;
      if (found.length === 2 && last > first && last - first < CitationLinker.MAX_RANGE) {
        for (let number = first; number <= last; number += 1) numbers.push(String(number));
      } else {
        numbers.push(...found.map(String));
      }
    }
    return numbers;
  }

  /** Builds one citation chip element. */
  #buildChip(citationNumber, onClick) {
    const chip = document.createElement('span');
    chip.className = 'citation-marker';
    chip.dataset.citation = citationNumber;
    chip.textContent = citationNumber;
    chip.addEventListener('click', onClick);
    return chip;
  }
}

/* ------------------------------------------------------------------- http */

/** Every HTTP call the interface makes. */
/** Sends the visitor back to the landing page once their session has ended. */
class SessionGuard {
  static redirectToSignIn() {
    window.location.assign('/#masuk');
  }
}

class ApiClient {
  /** Reads the system status, index statistics and backend availability. */
  async fetchStatus() {
    return this.#readJson(await fetch('/api/status'));
  }

  /** Lists top-level folders usable as retrieval filters. */
  async fetchFolders() {
    return this.#readJson(await fetch('/api/folders'));
  }

  /** Lists indexed documents, optionally filtered by ingest status. */
  async fetchDocuments(statusFilter) {
    const query = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : '';
    return this.#readJson(await fetch(`/api/documents${query}`));
  }

  /** Starts a background indexing run. */
  async startIngest(forceFullRebuild) {
    const response = await fetch('/api/ingest/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force_full_rebuild: forceFullRebuild }),
    });
    if (response.status === 409) return { started: false, message: t('app.ingestRunning') };
    return this.#readJson(response);
  }

  /** Polls progress of the current or most recent indexing run. */
  async fetchIngestStatus() {
    return this.#readJson(await fetch('/api/ingest/status'));
  }

  /** Retrieves passages without generating an answer. */
  async search(payload) {
    const response = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return this.#readJson(response);
  }

  /** Opens the streaming answer channel; returns the raw Response. */
  async openAnswerStream(payload, abortSignal) {
    const response = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: abortSignal,
    });
    return this.#requireStream(response);
  }

  /** Reopens an answer's stream, continuing after the last event received. */
  async openAnswerEvents(answerId, afterEventNumber, abortSignal) {
    const response = await fetch(
      `/api/answers/${encodeURIComponent(answerId)}/events?after=${afterEventNumber}`,
      { signal: abortSignal, cache: 'no-store' }
    );
    return this.#requireStream(response);
  }

  /**
   * Asks the server to stop generating an answer.
   *
   * Needed because generation no longer ends when the connection does.
   * Failure is reported rather than thrown: the reader has already stopped
   * listening, so there is nothing further to do about it.
   */
  async cancelAnswer(answerId) {
    try {
      const response = await fetch(`/api/answers/${encodeURIComponent(answerId)}/cancel`, {
        method: 'POST',
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  /** Returns a streaming response, or throws an error carrying its HTTP status. */
  #requireStream(response) {
    if (response.status === 401) SessionGuard.redirectToSignIn();
    if (!response.ok) {
      const error = new Error(t('app.requestRefused', { status: response.status }));
      error.status = response.status;
      throw error;
    }
    return response;
  }

  /** Lists saved conversations, most recently used first. */
  async fetchSessions() {
    return this.#readJson(await fetch('/api/sessions'));
  }

  /** Loads one conversation together with all of its turns. */
  async fetchSession(sessionId) {
    return this.#readJson(await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`));
  }

  /** Starts a new conversation. */
  async createSession(title) {
    const response = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title ?? null }),
    });
    return this.#readJson(response);
  }

  /** Renames a conversation. */
  async renameSession(sessionId, title) {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    return this.#readJson(response);
  }

  /** Deletes a conversation and every turn in it. */
  async deleteSession(sessionId) {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    });
    return this.#readJson(response);
  }

  /** Asks the server to reveal or open a document on this machine. */
  async runFileAction(documentId, action) {
    const response = await fetch('/api/file-action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_id: documentId, action }),
    });
    return this.#readJson(response);
  }

  /** Reports who is signed in and with which role. */
  async fetchCurrentUser() {
    return this.#readJson(await fetch('/api/auth/me'));
  }

  /** Ends the session on the server. */
  async signOut() {
    return this.#readJson(await fetch('/api/auth/logout', { method: 'POST' }));
  }

  /** Parses a JSON response, raising the server's detail message on failure. */
  async #readJson(response) {
    const parsed = await response.json().catch(() => ({}));
    if (response.status === 401) SessionGuard.redirectToSignIn();
    if (!response.ok) {
      throw new Error(parsed.detail || parsed.message || `HTTP ${response.status}`);
    }
    return parsed;
  }
}

/** Decodes a Server-Sent Events body into `{name, payload, id}` objects. */
class ServerSentEventReader {
  /**
   * Reads `response` until it ends, invoking `handler` per event.
   *
   * Frames are separated by a blank line; partial frames are buffered until
   * the terminating newline pair arrives. Keep-alive comments carry no data
   * and are dropped here, but they still count as signs of life for the
   * watchdog. Returning normally does not mean the answer is complete — a
   * stalled or closed connection ends the read too, and the caller decides.
   */
  async readAll(response, handler) {
    const reader = response.body.getReader();
    const watchdog = new StreamStallWatchdog(reader);
    const decoder = new TextDecoder();
    let buffer = '';

    watchdog.start();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        watchdog.recordActivity();
        buffer += decoder.decode(value, { stream: true });

        let separatorIndex = buffer.indexOf('\n\n');
        while (separatorIndex !== -1) {
          const frame = buffer.slice(0, separatorIndex);
          buffer = buffer.slice(separatorIndex + 2);
          const event = this.#parseFrame(frame);
          if (event) handler(event);
          separatorIndex = buffer.indexOf('\n\n');
        }
      }
    } finally {
      watchdog.stop();
    }
  }

  /** Turns one raw frame into a typed event, or null for comments and junk. */
  #parseFrame(frame) {
    let eventName = 'message';
    let eventNumber = null;
    const dataLines = [];

    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      else if (line.startsWith('id:')) eventNumber = Number(line.slice(3).trim());
    }
    if (dataLines.length === 0) return null;

    try {
      return {
        name: eventName,
        payload: JSON.parse(dataLines.join('\n')),
        id: Number.isFinite(eventNumber) ? eventNumber : null,
      };
    } catch {
      return null;
    }
  }
}

/**
 * Cancels a stream read that has gone silent for too long.
 *
 * The server sends a keep-alive at least every ten seconds, so a much longer
 * silence means the connection died without closing — typical when a phone
 * switches network or wakes from sleep. Left alone, the read would wait for
 * minutes; cancelling it ends the read so the answer can be resumed instead.
 */
class StreamStallWatchdog {
  /** Silence tolerated before the connection is presumed dead. */
  static STALL_MILLISECONDS = 35000;

  /** Silence that, found on returning to the page, already means a dead link. */
  static SUSPECT_ON_RETURN_MILLISECONDS = 15000;

  constructor(streamReader) {
    this.streamReader = streamReader;
    this.timer = null;
    this.lastActivityAt = Date.now();
    this.handleStall = this.handleStall.bind(this);
    this.handleVisibilityChange = this.handleVisibilityChange.bind(this);
  }

  /** Begins watching the stream. */
  start() {
    document.addEventListener('visibilitychange', this.handleVisibilityChange);
    this.recordActivity();
  }

  /** Notes that data arrived, restarting the silence countdown. */
  recordActivity() {
    this.lastActivityAt = Date.now();
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(this.handleStall, StreamStallWatchdog.STALL_MILLISECONDS);
  }

  /** Stops watching; called once the read has ended either way. */
  stop() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    document.removeEventListener('visibilitychange', this.handleVisibilityChange);
  }

  /**
   * Checks the stream as the reader returns to the page.
   *
   * A phone suspends the network while its screen is off, and the connection
   * rarely survives. When nothing has arrived for longer than the keep-alive
   * interval, sitting out the full stall timeout would only delay the resume.
   */
  handleVisibilityChange() {
    if (document.visibilityState !== 'visible') return;
    const silence = Date.now() - this.lastActivityAt;
    if (silence > StreamStallWatchdog.SUSPECT_ON_RETURN_MILLISECONDS) this.handleStall();
  }

  /** Abandons the silent read, which makes the pending read() finish. */
  handleStall() {
    this.stop();
    this.streamReader.cancel().catch(StreamStallWatchdog.ignoreFailure);
  }

  /** Rejection handler for a cancel whose failure changes nothing. */
  static ignoreFailure() {}
}

/**
 * A pause before reconnecting that ends as soon as retrying makes sense.
 *
 * While the page is hidden — screen locked, another app in front — a mobile
 * browser suspends the network, so attempts made then are wasted. The pause
 * therefore outlasts its delay until the page is visible again, and returning
 * to the page, or the network coming back, ends it at once.
 */
class ReconnectPause {
  constructor(delayMilliseconds, abortSignal) {
    this.delayMilliseconds = delayMilliseconds;
    this.abortSignal = abortSignal;
    this.timer = null;
    this.isSettled = false;
    this.resolvePause = null;
    this.rejectPause = null;
    this.handleElapsed = this.handleElapsed.bind(this);
    this.handleVisibilityChange = this.handleVisibilityChange.bind(this);
    this.handleOnline = this.handleOnline.bind(this);
    this.handleAbort = this.handleAbort.bind(this);
  }

  /** Resolves when it is time to retry; rejects with an AbortError if stopped. */
  wait() {
    return new Promise(this.#begin.bind(this));
  }

  /** Arms the timer and the listeners that can end the pause early. */
  #begin(resolve, reject) {
    this.resolvePause = resolve;
    this.rejectPause = reject;
    if (this.abortSignal.aborted) {
      this.handleAbort();
      return;
    }
    this.timer = setTimeout(this.handleElapsed, this.delayMilliseconds);
    document.addEventListener('visibilitychange', this.handleVisibilityChange);
    window.addEventListener('online', this.handleOnline);
    this.abortSignal.addEventListener('abort', this.handleAbort);
  }

  /** The delay is over: retry now, or keep waiting until the page is visible. */
  handleElapsed() {
    this.timer = null;
    if (document.visibilityState !== 'hidden') this.#settle(true);
  }

  /** The reader has come back to the page: retry immediately. */
  handleVisibilityChange() {
    if (document.visibilityState === 'visible') this.#settle(true);
  }

  /** The network is back: retry immediately if the page is in view. */
  handleOnline() {
    if (document.visibilityState !== 'hidden') this.#settle(true);
  }

  /** The reader pressed stop while waiting. */
  handleAbort() {
    this.#settle(false);
  }

  /** Ends the pause exactly once, removing every listener it added. */
  #settle(shouldRetry) {
    if (this.isSettled) return;
    this.isSettled = true;
    if (this.timer) clearTimeout(this.timer);
    document.removeEventListener('visibilitychange', this.handleVisibilityChange);
    window.removeEventListener('online', this.handleOnline);
    this.abortSignal.removeEventListener('abort', this.handleAbort);
    if (shouldRetry) this.resolvePause();
    else this.rejectPause(new DOMException(t('app.stoppedByUser'), 'AbortError'));
  }
}

/** A failure to follow an answer whose message is complete as it stands. */
class AnswerStreamError extends Error {}

/**
 * Follows one answer from question to `done`, reconnecting whenever needed.
 *
 * The server generates each answer independently of the connection that asked
 * for it and numbers every event. When the connection drops — which on a
 * phone happens simply by locking the screen during a long answer — this class
 * reconnects and requests only the events after the last one it received, so
 * the text carries on exactly where it stopped instead of failing with
 * "network error".
 */
class AnswerStreamFollower {
  /** Waits between consecutive attempts; the last value repeats. */
  static RETRY_DELAYS_MILLISECONDS = [1000, 2000, 4000, 8000, 15000];

  /** Attempts in a row that bring no new events before giving up. */
  static MAXIMUM_FUTILE_ATTEMPTS = 8;

  /**
   * `callbacks.onEvent(event)` receives every answer event;
   * `callbacks.onReconnecting()` and `callbacks.onReconnected()` bracket each
   * period without a connection, so the view can say what is happening.
   */
  constructor(apiClient, eventReader, callbacks) {
    this.api = apiClient;
    this.eventReader = eventReader;
    this.callbacks = callbacks;
    //: Known once the server has accepted the question; needed to resume.
    this.answerId = null;
    this.lastEventNumber = 0;
    this.hasFinished = false;
    this.handleFrame = this.handleFrame.bind(this);
  }

  /** Asks the question, then follows the answer until it is complete. */
  async follow(payload, abortSignal) {
    try {
      const response = await this.api.openAnswerStream(payload, abortSignal);
      await this.eventReader.readAll(response, this.handleFrame);
    } catch (error) {
      // Without an answer identifier there is nothing to resume.
      if (error.name === 'AbortError' || !this.answerId) throw error;
    }
    if (!this.hasFinished && !this.answerId) {
      throw new Error(t('app.lostBeforeAccepted'));
    }
    await this.#resumeUntilFinished(abortSignal);
  }

  /** Records the stream position, then passes answer events on. */
  handleFrame(event) {
    if (event.name === 'accepted') {
      this.answerId = event.payload.answer_id;
      return;
    }
    if (event.id !== null) this.lastEventNumber = event.id;
    if (event.name === 'done') this.hasFinished = true;
    this.callbacks.onEvent(event);
  }

  /** Reconnects until `done` arrives, giving up only after repeated futility. */
  async #resumeUntilFinished(abortSignal) {
    let futileAttempts = 0;
    while (!this.hasFinished) {
      if (futileAttempts >= AnswerStreamFollower.MAXIMUM_FUTILE_ATTEMPTS) {
        throw new AnswerStreamError(t('app.cannotReconnect'));
      }

      this.callbacks.onReconnecting();
      await new ReconnectPause(this.#retryDelay(futileAttempts), abortSignal).wait();

      const lastEventNumberBefore = this.lastEventNumber;
      try {
        const response = await this.api.openAnswerEvents(
          this.answerId, this.lastEventNumber, abortSignal
        );
        this.callbacks.onReconnected();
        await this.eventReader.readAll(response, this.handleFrame);
      } catch (error) {
        if (error.name === 'AbortError') throw error;
        if (error.status === 404) {
          throw new AnswerStreamError(t('app.answerGone'));
        }
      }
      futileAttempts = this.lastEventNumber > lastEventNumberBefore ? 0 : futileAttempts + 1;
    }
  }

  /** The wait before attempt number `futileAttempts + 1`. */
  #retryDelay(futileAttempts) {
    const delays = AnswerStreamFollower.RETRY_DELAYS_MILLISECONDS;
    return delays[Math.min(futileAttempts, delays.length - 1)];
  }
}

/* ------------------------------------------------------------------ toast */

/** Shows brief messages at the bottom of the window. */
class Toast {
  constructor(element) {
    this.element = element;
    this.hideTimer = null;
  }

  /** Displays `message` for a few seconds. */
  show(message) {
    this.element.textContent = message;
    this.element.classList.remove('hidden');
    if (this.hideTimer) clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(this.hide.bind(this), 4200);
  }

  /** Hides the toast immediately. */
  hide() {
    this.element.classList.add('hidden');
  }
}

/* ------------------------------------------------------------- clipboard */

/**
 * Copies text to the clipboard, with a fallback for non-secure origins.
 *
 * `navigator.clipboard` is unavailable over plain HTTP unless the origin is
 * localhost — and this app is reached over the LAN by IP, which is exactly that
 * case. The `execCommand` fallback is deprecated but remains the only thing
 * that works there, so both paths are kept.
 */
class ClipboardWriter {
  /** Copies `text`, returning true when it reached the clipboard. */
  async copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        // Fall through to the legacy path rather than failing outright.
      }
    }
    return this.#copyViaHiddenTextarea(text);
  }

  /** Legacy copy path: select text in an off-screen textarea and execute copy. */
  #copyViaHiddenTextarea(text) {
    const holder = document.createElement('textarea');
    holder.value = text;
    holder.setAttribute('readonly', '');
    holder.style.position = 'fixed';
    holder.style.top = '-1000px';
    holder.style.opacity = '0';
    document.body.appendChild(holder);
    holder.select();

    let succeeded = false;
    try {
      succeeded = document.execCommand('copy');
    } catch {
      succeeded = false;
    }
    document.body.removeChild(holder);
    return succeeded;
  }
}

/* --------------------------------------------------------- session panel */

/**
 * Lists saved conversations and drives creating, opening and deleting them.
 *
 * The panel owns no conversation state itself; selecting a session raises a
 * callback and the conversation view decides what to load, which keeps the two
 * from having to agree on message formats.
 */
class SessionPanel {
  constructor(apiClient, formatter, toast, onSessionOpened) {
    this.api = apiClient;
    this.formatter = formatter;
    this.toast = toast;
    this.onSessionOpened = onSessionOpened;
    this.escaper = new HtmlEscaper();
    this.sessions = [];
    this.activeSessionId = null;
    this.listElement = document.getElementById('session-list');

    document.getElementById('button-new-session')
      .addEventListener('click', this.handleNewSession.bind(this));
  }

  /** Reloads the session list and repaints it. */
  async refresh() {
    try {
      const result = await this.api.fetchSessions();
      this.sessions = result.sessions || [];
      this.render();
    } catch (error) {
      this.toast.show(t('app.loadConversationsFailed', { error: error.message }));
    }
  }

  /** Marks which session is currently open, without refetching. */
  setActiveSession(sessionId) {
    this.activeSessionId = sessionId;
    SessionPanel.rememberOpenSession(sessionId);
    this.render();
  }

  /** Browser-storage key for the open conversation, kept across a reload. */
  static OPEN_SESSION_KEY = 'exim-rag.open-session';

  /**
   * Remembers the open conversation for this tab.
   *
   * Switching the interface language reloads the page; without this the
   * reader would land on an empty welcome screen instead of their thread.
   */
  static rememberOpenSession(sessionId) {
    try {
      if (sessionId) window.sessionStorage.setItem(SessionPanel.OPEN_SESSION_KEY, sessionId);
      else window.sessionStorage.removeItem(SessionPanel.OPEN_SESSION_KEY);
    } catch {
      // Blocked storage: a reload simply starts on the welcome screen.
    }
  }

  /** The conversation open before the last reload, if it still exists. */
  rememberedOpenSession() {
    let sessionId = null;
    try {
      sessionId = window.sessionStorage.getItem(SessionPanel.OPEN_SESSION_KEY);
    } catch {
      return null;
    }
    return this.sessions.some((session) => session.session_id === sessionId) ? sessionId : null;
  }

  /** Paints the session list. */
  render() {
    if (this.sessions.length === 0) {
      this.listElement.innerHTML = `<p class="session-empty">${this.escaper.escape(t('app.noConversations'))}</p>`;
      return;
    }
    this.listElement.innerHTML = this.sessions.map(this.#renderRow.bind(this)).join('');
    for (const row of this.listElement.querySelectorAll('.session-open')) {
      row.addEventListener('click', this.handleOpen.bind(this));
    }
    for (const button of this.listElement.querySelectorAll('.session-delete')) {
      button.addEventListener('click', this.handleDelete.bind(this));
    }
    for (const button of this.listElement.querySelectorAll('.session-rename')) {
      button.addEventListener('click', this.handleRename.bind(this));
    }
  }

  /** Renders one session row. */
  #renderRow(session) {
    const isActive = session.session_id === this.activeSessionId;
    const exchanges = Math.ceil((session.turn_count || 0) / 2);
    return `
      <div class="session-row ${isActive ? 'active' : ''}">
        <button class="session-open" data-session-id="${this.escaper.escape(session.session_id)}"
                title="${this.escaper.escape(session.title)}">
          <span class="session-title">${this.escaper.escape(session.title)}</span>
          <span class="session-meta">${this.escaper.escape(t('app.sessionMeta', { count: exchanges, time: this.formatter.timestamp(session.updated_at) }))}</span>
        </button>
        <div class="session-actions">
          <button class="session-rename" data-session-id="${this.escaper.escape(session.session_id)}"
                  title="${this.escaper.escape(t('app.rename'))}">✎</button>
          <button class="session-delete" data-session-id="${this.escaper.escape(session.session_id)}"
                  title="${this.escaper.escape(t('app.deleteConversation'))}">✕</button>
        </div>
      </div>`;
  }

  /** Creates a fresh conversation and opens it. */
  async handleNewSession() {
    try {
      const session = await this.api.createSession(null);
      await this.refresh();
      this.setActiveSession(session.session_id);
      if (this.onSessionOpened) this.onSessionOpened(session.session_id, true);
    } catch (error) {
      this.toast.show(t('app.createConversationFailed', { error: error.message }));
    }
  }

  /** Opens the clicked session. */
  handleOpen(clickEvent) {
    const sessionId = clickEvent.currentTarget.dataset.sessionId;
    this.setActiveSession(sessionId);
    if (this.onSessionOpened) this.onSessionOpened(sessionId, false);
  }

  /** Renames the clicked session after prompting for a new title. */
  async handleRename(clickEvent) {
    clickEvent.stopPropagation();
    const sessionId = clickEvent.currentTarget.dataset.sessionId;
    const current = this.sessions.find((s) => s.session_id === sessionId);
    const title = window.prompt(t('app.renamePrompt'), current ? current.title : '');
    if (!title) return;
    try {
      await this.api.renameSession(sessionId, title);
      await this.refresh();
    } catch (error) {
      this.toast.show(t('app.renameFailed', { error: error.message }));
    }
  }

  /** Deletes the clicked session after confirmation. */
  async handleDelete(clickEvent) {
    clickEvent.stopPropagation();
    const sessionId = clickEvent.currentTarget.dataset.sessionId;
    const session = this.sessions.find((s) => s.session_id === sessionId);
    const label = session ? session.title : t('app.thisConversation');
    if (!window.confirm(t('app.confirmDelete', { label }))) {
      return;
    }
    try {
      await this.api.deleteSession(sessionId);
      this.toast.show(t('app.conversationDeleted'));
      const wasActive = sessionId === this.activeSessionId;
      await this.refresh();
      if (wasActive) {
        this.activeSessionId = null;
        SessionPanel.rememberOpenSession(null);
        if (this.onSessionOpened) this.onSessionOpened(null, true);
      }
    } catch (error) {
      this.toast.show(t('app.deleteFailed', { error: error.message }));
    }
  }
}

/* ----------------------------------------------------------- status panel */

/** Renders the sidebar's system status and keeps the backend list current. */
class StatusPanel {
  constructor(apiClient, formatter, toast) {
    this.api = apiClient;
    this.formatter = formatter;
    this.toast = toast;
    this.backendStatusesByName = new Map();
  }

  /** Fetches status and repaints every sidebar field. */
  async refresh() {
    let status;
    try {
      status = await this.api.fetchStatus();
    } catch (error) {
      this.toast.show(t('app.loadStatusFailed', { error: error.message }));
      return null;
    }

    this.#renderSource(status.source);
    this.#renderIndex(status.index, status.ocr);
    this.#renderModels(status);
    this.#renderBackends(status.language_models);
    this.#renderClientScope(status.client);
    return status;
  }

  /** Publishes whether reveal/open are usable, and says so in the sidebar. */
  #renderClientScope(client) {
    const isLocal = Boolean(client && client.is_local);
    AnswerRenderer.isLocalClient = isLocal;

    const line = document.getElementById('client-line');
    if (!line) return;
    line.textContent = isLocal
      ? t('app.accessLocal')
      : t('app.accessNetwork', { address: client ? client.address : '?' });
  }

  /** Paints the corpus location and read-only assurance (admins only). */
  #renderSource(source) {
    // The server withholds the corpus location from guests.
    document.getElementById('panel-source').classList.toggle('hidden', !source);
    if (!source) return;
    const pathElement = document.getElementById('source-path');
    pathElement.textContent = source.directory;
    if (!source.exists) {
      pathElement.textContent += `  ${t('app.notFound')}`;
    }
  }

  /** Paints document, chunk, size and failure counters, and what OCR made of scans. */
  #renderIndex(index, ocr) {
    document.getElementById('stat-documents').textContent = this.formatter.count(index.document_count);
    document.getElementById('stat-chunks').textContent = this.formatter.count(index.chunk_count);
    document.getElementById('stat-megabytes').textContent = this.formatter.count(index.total_megabytes);
    document.getElementById('stat-failed').textContent = this.formatter.count(index.failed_count);
    document.getElementById('stat-last-indexed').textContent =
      t('app.lastIndexed', { time: this.formatter.timestamp(index.last_indexed_at) });

    const scannedLine = document.getElementById('stat-scanned');
    const scanned = index.scanned_section_count;
    if (scanned === 0) {
      scannedLine.classList.add('hidden');
      return;
    }
    scannedLine.classList.remove('hidden');
    if (!ocr.available) {
      scannedLine.classList.add('warning-line');
      scannedLine.textContent = t('app.scannedNeedOcr', { count: this.formatter.count(scanned) });
      return;
    }
    // OCR already reads scans; a page it leaves unread is blank, or lies past
    // the per-document OCR page limit.
    const unread = scanned - index.ocr_section_count;
    scannedLine.classList.remove('warning-line');
    scannedLine.textContent =
      t('app.scannedRead', {
        scanned: this.formatter.count(scanned),
        read: this.formatter.count(index.ocr_section_count),
      }) +
      (unread > 0 ? t('app.scannedUnread', { count: this.formatter.count(unread) }) : '.');
  }

  /** Paints the hardware, embedding, reranking and OCR lines. */
  #renderModels(status) {
    document.getElementById('hardware-line').textContent = t('app.accelerator', { name: status.hardware.accelerator });
    document.getElementById('embedding-line').textContent =
      `Embedding: ${status.models.embedding_model} (${status.models.embedding_device})`;

    const rerankerLine = document.getElementById('reranker-line');
    rerankerLine.textContent = status.models.reranking_enabled
      ? `Reranker: ${status.models.reranking_model}`
      : t('app.rerankerOff');

    const ocrLine = document.getElementById('ocr-line');
    ocrLine.textContent = status.ocr.available
      ? t('app.ocrOn')
      : (status.ocr.reason ? `OCR: ${status.ocr.reason}` : t('app.ocrOff'));
  }

  /** Rebuilds the backend dropdown, marking unavailable backends. */
  #renderBackends(languageModels) {
    const select = document.getElementById('select-backend');
    const previousChoice = select.value || languageModels.active_backend;
    this.backendStatusesByName.clear();
    select.innerHTML = '';

    for (const backend of languageModels.backends) {
      this.backendStatusesByName.set(backend.backend_name, backend);
      const option = document.createElement('option');
      option.value = backend.backend_name;
      // The model belongs on the option itself: it is the basis for choosing
      // between backends, and putting it only in the detail line below means it
      // is read after the choice, not before.
      const readiness = backend.is_available ? '' : `  ${t('app.notReady')}`;
      option.textContent =
        `${this.#backendLabel(backend.backend_name)} — ${backend.model_name}${readiness}`;
      select.appendChild(option);
    }

    select.value = this.backendStatusesByName.has(previousChoice)
      ? previousChoice
      : languageModels.active_backend;
    this.renderSelectedBackendDetail();
  }

  /** Friendly display name for a backend identifier. */
  #backendLabel(backendName) {
    // Kept short: the model name is appended after these, and the
    // sidebar dropdown is only so wide.
    const labels = {
      ollama: t('app.ollamaLocal'),
      claude_cli: 'Claude Code',
      codex_cli: 'Codex',
      antigravity_cli: 'Antigravity',
    };
    return labels[backendName] || backendName;
  }

  /** Paints the availability line beneath the backend dropdown. */
  renderSelectedBackendDetail() {
    const select = document.getElementById('select-backend');
    const detailElement = document.getElementById('backend-status');
    const backend = this.backendStatusesByName.get(select.value);

    if (!backend) {
      detailElement.textContent = '';
      return;
    }
    const indicatorClass = backend.is_available ? 'online' : 'offline';
    const escaper = new HtmlEscaper();
    // Sending document excerpts off-machine is a decision the
    // operator should make knowingly, so it is stated on the control itself.
    const privacyNote = backend.runs_locally
      ? `<span class="privacy-note local">${escaper.escape(t('app.privacyLocal'))}</span>`
      : `<span class="privacy-note remote">${escaper.escape(t('app.privacyRemote'))}</span>`;

    detailElement.innerHTML =
      `<span class="indicator ${indicatorClass}"></span>` +
      `<strong>${escaper.escape(backend.model_name)}</strong>` +
      `<br><span class="backend-detail">${escaper.escape(backend.detail)}</span><br>` +
      privacyNote;
  }
}

/* ----------------------------------------------------------- ingest panel */

/** Drives index building and mirrors its progress into the sidebar. */
class IngestPanel {
  /** Milliseconds between progress polls while a run is active. */
  static POLL_INTERVAL_MILLISECONDS = 1200;

  constructor(apiClient, formatter, toast, onRunFinished) {
    this.api = apiClient;
    this.formatter = formatter;
    this.toast = toast;
    this.onRunFinished = onRunFinished;
    this.pollTimer = null;
    // Assume the restricted role until the server says otherwise, so a guest
    // never glimpses a working control during page load.
    this.isAdmin = false;

    document.getElementById('button-ingest')
      .addEventListener('click', this.handleIncrementalIngest.bind(this));
    document.getElementById('button-rebuild')
      .addEventListener('click', this.handleFullRebuild.bind(this));
  }

  /** Records whether the signed-in user may change the index. */
  setAdministrator(isAdmin) {
    this.isAdmin = isAdmin;
    for (const id of ['button-ingest', 'button-rebuild']) {
      document.getElementById(id).classList.toggle('restricted', !isAdmin);
    }
  }

  /**
   * Tells a guest they lack the right to change the index.
   *
   * The buttons stay visible on purpose: guests should know the index can be
   * refreshed and by whom. The server enforces the same rule independently.
   */
  #showPermissionNotice(message) {
    const notice = document.getElementById('permission-notice');
    notice.innerHTML =
      '<span class="permission-icon" aria-hidden="true">🔒</span>' +
      `<span>${new HtmlEscaper().escape(message)}</span>`;
    notice.classList.remove('hidden');
    // Restarted so a repeated click visibly re-announces the message.
    notice.classList.remove('shake');
    void notice.offsetWidth;
    notice.classList.add('shake');
  }

  /** Starts an incremental run that only re-reads changed documents. */
  async handleIncrementalIngest() {
    if (!this.isAdmin) {
      this.#showPermissionNotice(t('app.noRightUpdate'));
      return;
    }
    await this.#start(false);
  }

  /** Starts a full rebuild after confirming, since it discards the index. */
  async handleFullRebuild() {
    if (!this.isAdmin) {
      this.#showPermissionNotice(t('app.noRightRebuild'));
      return;
    }
    const confirmed = window.confirm(t('app.confirmRebuild'));
    if (confirmed) await this.#start(true);
  }

  /** Requests a run and begins polling for progress. */
  async #start(forceFullRebuild) {
    this.#setButtonsEnabled(false);
    try {
      const result = await this.api.startIngest(forceFullRebuild);
      // The server phrases its reply in Indonesian; only a refusal is shown as is.
      this.toast.show(result.started === false ? result.message : t('app.ingestStarted'));
    } catch (error) {
      this.toast.show(t('app.ingestStartFailed', { error: error.message }));
      this.#setButtonsEnabled(true);
      return;
    }
    document.getElementById('ingest-progress').classList.remove('hidden');
    this.#scheduleNextPoll();
  }

  /** Resumes polling on page load if a run is already in flight. */
  async resumeIfRunning() {
    try {
      const progress = await this.api.fetchIngestStatus();
      if (progress.is_running) {
        document.getElementById('ingest-progress').classList.remove('hidden');
        this.#setButtonsEnabled(false);
        this.#scheduleNextPoll();
      }
    } catch {
      /* Status is unavailable before the first run; nothing to resume. */
    }
  }

  /** Queues the next poll tick. */
  #scheduleNextPoll() {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(this.#poll.bind(this), IngestPanel.POLL_INTERVAL_MILLISECONDS);
  }

  /** Reads progress once and either repaints or finishes the run. */
  async #poll() {
    let progress;
    try {
      progress = await this.api.fetchIngestStatus();
    } catch {
      this.#scheduleNextPoll();
      return;
    }

    this.#render(progress);
    if (progress.is_running) {
      this.#scheduleNextPoll();
      return;
    }

    this.#setButtonsEnabled(true);
    if (progress.error_message) {
      this.toast.show(t('app.ingestFailed', { error: progress.error_message }));
    } else if (progress.phase === 'completed') {
      this.toast.show(t('app.ingestDone', {
        duration: this.formatter.duration(progress.elapsed_seconds),
        count: this.formatter.count(progress.documents_indexed),
      }));
    }
    if (this.onRunFinished) this.onRunFinished();
  }

  /** Paints the progress bar, counters and run log. */
  #render(progress) {
    const total = Math.max(1, progress.documents_total);
    const percentage = Math.min(100, Math.round((progress.documents_processed / total) * 100));

    document.getElementById('progress-fill').style.width = `${percentage}%`;
    document.getElementById('progress-caption').textContent =
      `${this.#phaseLabel(progress.phase)} — ${percentage}%`;
    document.getElementById('progress-detail').textContent = t('app.progressDetail', {
      processed: this.formatter.count(progress.documents_processed),
      total: this.formatter.count(progress.documents_total),
      indexed: this.formatter.count(progress.documents_indexed),
      skipped: this.formatter.count(progress.documents_skipped),
      failed: this.formatter.count(progress.documents_failed),
      chunks: this.formatter.count(progress.chunks_created),
      duration: this.formatter.duration(progress.elapsed_seconds),
    });

    const logElement = document.getElementById('progress-log-text');
    const combinedLog = (progress.recent_messages || []).join('\n');
    if (logElement.textContent !== combinedLog) {
      logElement.textContent = combinedLog;
      logElement.scrollTop = logElement.scrollHeight;
    }

    const currentDocument = progress.current_document;
    if (currentDocument) {
      document.getElementById('progress-caption').textContent =
        `${this.#phaseLabel(progress.phase)} — ${percentage}% · ${currentDocument}`;
    }
  }

  /** Translates a pipeline phase identifier into a readable label. */
  #phaseLabel(phase) {
    const key = `app.phase.${phase}`;
    const label = t(key);
    return label === key ? phase : label;
  }

  /** Enables or disables both ingest buttons together. */
  #setButtonsEnabled(enabled) {
    document.getElementById('button-ingest').disabled = !enabled;
    document.getElementById('button-rebuild').disabled = !enabled;
  }
}

/* -------------------------------------------------------- answer renderer */

/** Renders one streaming answer: status line, text, citations and footer. */
class AnswerRenderer {
  /**
   * Whether the viewer is at the machine running the server.
   *
   * Static because every answer card needs it and renderers are created one
   * per answer; threading it through each constructor would add a parameter to
   * every call site for a value that is fixed for the whole page.
   */
  static isLocalClient = false;

  constructor(containerElement, apiClient, toast, clipboard) {
    this.container = containerElement;
    this.api = apiClient;
    this.toast = toast;
    this.clipboard = clipboard || new ClipboardWriter();
    this.citations = [];
    this.escaper = new HtmlEscaper();
    this.formatter = new ValueFormatter();
    this.markdown = new MarkdownRenderer();
    this.citationLinker = new CitationLinker();
    this.accumulatedText = '';
    this.statusMessage = '';
    this.citationCardsByNumber = new Map();

    this.container.innerHTML =
      '<div class="answer-status" id="answer-status-line">' +
      `<span class="spinner"></span><span>${this.escaper.escape(t('app.searchingDocuments'))}</span></div>` +
      '<div class="answer-text"></div>';
    this.statusElement = this.container.querySelector('.answer-status');
    this.textElement = this.container.querySelector('.answer-text');
  }

  /** Routes one stream event to the matching render method. */
  handleEvent(event) {
    if (event.name === 'sources') {
      this.renderSearchNote(event.payload);
      this.renderCitations(event.payload.citations || []);
    }
    else if (event.name === 'status') this.setStatusMessage(event.payload.message);
    else if (event.name === 'token') this.appendText(event.payload.text || '');
    else if (event.name === 'error') this.renderError(event.payload.message);
    else if (event.name === 'done') this.finish(event.payload);
  }

  /**
   * Says which country's documents were searched, and with what wording.
   *
   * The question was restated in that country's language before searching;
   * showing the restatement lets the reader see, and judge, what was sought.
   */
  renderSearchNote(payload) {
    if (!payload.search_query) return;
    const note = document.createElement('div');
    note.className = 'search-note';
    note.textContent = t('app.searchedAs', {
      country: interfaceLanguage.countryName(payload.country),
      query: payload.search_query,
    });
    this.container.insertBefore(note, this.container.firstChild);
  }

  /** Replaces the status line's text while keeping the spinner. */
  setStatusMessage(message) {
    this.statusMessage = message;
    this.#writeStatusLabel(message);
  }

  /**
   * Says the connection dropped and is being restored.
   *
   * Temporary by design: the previous status message is kept and put back by
   * `clearConnectionNotice`, so the line does not go on claiming a problem
   * after the answer has resumed.
   */
  showConnectionNotice() {
    this.statusElement.classList.add('reconnecting');
    this.#writeStatusLabel(t('app.reconnecting'));
  }

  /** Restores the status line once the connection is back. */
  clearConnectionNotice() {
    this.statusElement.classList.remove('reconnecting');
    this.#writeStatusLabel(this.statusMessage || t('app.searchingDocuments'));
  }

  /** Writes the status line's text, if the line is still on screen. */
  #writeStatusLabel(text) {
    const label = this.statusElement.querySelector('span:last-child');
    if (label) label.textContent = text;
  }

  /**
   * Appends streamed text and repaints the answer as rendered Markdown.
   *
   * The whole answer is re-rendered on every chunk rather than appended to,
   * because Markdown is not resumable — a heading, list or table is only
   * recognisable once its full line has arrived. Answers run to a few kilobytes
   * at most, so the repeated parse is imperceptible.
   */
  appendText(textFragment) {
    this.accumulatedText += textFragment;
    this.textElement.innerHTML = this.markdown.render(this.accumulatedText);
    this.citationLinker.apply(this.textElement, this.handleCitationClick.bind(this));
  }

  /** Expands the evidence panel, then scrolls to and highlights one card. */
  handleCitationClick(clickEvent) {
    const citationNumber = clickEvent.currentTarget.dataset.citation;
    const card = this.citationCardsByNumber.get(String(citationNumber));
    if (!card) return;

    // The panel is collapsed by default, and a card inside a closed <details>
    // has no layout — scrolling to it would silently do nothing.
    const section = card.closest('details.sources-section');
    if (section) section.open = true;

    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('highlighted');
    setTimeout(() => card.classList.remove('highlighted'), 1600);
  }

  /**
   * Builds the evidence panel beneath the answer, collapsed by default.
   *
   * A native `<details>` rather than a scripted toggle: it opens and closes
   * without JavaScript, is keyboard accessible, and keeps no state that could
   * drift out of step with the DOM. Collapsed initially because the answer is
   * what the reader came for — the sources are there to be checked, not read
   * first — and six cards of excerpt would otherwise bury the next question.
   */
  renderCitations(citations) {
    this.citations = citations;
    if (citations.length === 0) return;

    const section = document.createElement('details');
    section.className = 'sources-section';

    const summary = document.createElement('summary');
    summary.className = 'sources-heading';
    summary.textContent = t('app.sources', { count: citations.length });
    section.appendChild(summary);

    for (const citation of citations) {
      const card = this.#buildCitationCard(citation);
      section.appendChild(card);
      this.citationCardsByNumber.set(String(citation.citation_number), card);
    }
    this.container.appendChild(section);
  }

  /** Constructs one citation card element. */
  #buildCitationCard(citation) {
    const card = document.createElement('div');
    card.className = 'source-card';

    const badges = citation.retrieval_methods
      .map((method) => `<span class="badge ${method === 'semantic' ? 'semantic' : 'keyword'}">${this.escaper.escape(t(method === 'semantic' ? 'app.semantic' : 'app.keyword'))}</span>`)
      .join('');
    const ocrBadge = citation.produced_by_ocr ? '<span class="badge ocr">OCR</span>' : '';

    // Where a passage names an external page — the original on the source site —
    // that live page is what the reader actually wants, not the local copy.
    const externalLinks = (citation.external_links || [])
      .map((link) =>
        `<a class="external-link" href="${this.escaper.escape(link.url)}"
            target="_blank" rel="noopener noreferrer"
            title="${this.escaper.escape(link.url)}">${this.escaper.escape(link.label)} ↗</a>`)
      .join('');
    const externalRow = externalLinks
      ? `<div class="source-external">${this.escaper.escape(t('app.openOriginal'))} ${externalLinks}</div>`
      : '';

    card.innerHTML = `
      <div class="source-card-header">
        <span class="source-number">${citation.citation_number}</span>
        <span class="source-name">${this.escaper.escape(citation.document_name)}</span>
      </div>
      <div class="source-meta">${this.escaper.escape(citation.relative_path)} · ${this.escaper.escape(interfaceLanguage.sectionLabel(citation.section_label))}</div>
      <div class="badge-row">${badges}${ocrBadge}<span class="badge">${this.escaper.escape(t('app.score', { score: citation.relevance_score }))}</span></div>
      ${externalRow}
      <div class="source-excerpt">${this.escaper.escape(citation.excerpt)}</div>
      <div class="source-actions">
        <a class="link-button" download
           href="/api/documents/${encodeURIComponent(citation.document_id)}/download">${this.escaper.escape(t('app.downloadDocument'))}</a>
        ${AnswerRenderer.isLocalClient
          ? `<button class="link-button" data-action="reveal">${this.escaper.escape(t('app.showInFolder'))}</button>` +
            `<button class="link-button" data-action="open">${this.escaper.escape(t('app.openDocument'))}</button>`
          : ''}
        <button class="link-button" data-action="copy-excerpt">${this.escaper.escape(t('app.copyExcerpt'))}</button>
        <button class="link-button" data-action="copy-path">${this.escaper.escape(t('app.copyLocation'))}</button>
      </div>`;

    for (const button of card.querySelectorAll('button.link-button')) {
      button.dataset.documentId = citation.document_id;
      button.dataset.relativePath = citation.relative_path;
      // Assigned as properties so the text needs no HTML escaping.
      button.dataset.excerpt = citation.excerpt || '';
      button.dataset.documentName = citation.document_name || '';
      button.dataset.sectionLabel = interfaceLanguage.sectionLabel(citation.section_label);
      button.addEventListener('click', this.handleSourceAction.bind(this));
    }
    return card;
  }

  /** Handles reveal / open / copy actions on a citation card. */
  async handleSourceAction(clickEvent) {
    const button = clickEvent.currentTarget;
    const action = button.dataset.action;

    if (action === 'copy-path') {
      const copied = await this.clipboard.copy(button.dataset.relativePath || '');
      this.toast.show(t(copied ? 'app.locationCopied' : 'app.copyFailed'));
      return;
    }
    if (action === 'copy-excerpt') {
      // Copied with its provenance attached, so a pasted quotation still says
      // which document and page it came from.
      const attributed =
        `"${button.dataset.excerpt || ''}"\n` +
        `— ${button.dataset.documentName} (${button.dataset.sectionLabel})\n` +
        `  ${button.dataset.relativePath}`;
      const copied = await this.clipboard.copy(attributed);
      this.toast.show(t(copied ? 'app.excerptCopied' : 'app.copyFailed'));
      return;
    }
    try {
      await this.api.runFileAction(button.dataset.documentId, action);
    } catch (error) {
      this.toast.show(t('app.openFailed', { error: error.message }));
    }
  }

  /** Shows an error banner without discarding text already streamed. */
  renderError(message) {
    const banner = document.createElement('div');
    banner.className = 'answer-error';
    banner.textContent = message;
    this.textElement.insertAdjacentElement('afterend', banner);
  }

  /** Removes the spinner and writes the timing footer with copy actions. */
  finish(summary) {
    if (this.statusElement && this.statusElement.parentNode) this.statusElement.remove();
    if (!this.accumulatedText.trim()) {
      this.textElement.textContent = t('app.noAnswer');
    }

    const parts = [`${this.formatter.duration(summary.elapsed_seconds)}`];
    if (summary.passage_count !== undefined) parts.push(t('app.sourceTotal', { count: summary.passage_count }));
    if (summary.model_name) {
      parts.push(`${summary.backend_name} · ${summary.model_name}`);
    }

    const footer = document.createElement('div');
    footer.className = 'answer-footer';
    footer.innerHTML =
      `<span class="answer-meta">${this.escaper.escape(parts.join(' · '))}</span>` +
      `<span class="answer-copy-actions">` +
      `<button class="link-button" data-copy="answer">${this.escaper.escape(t('app.copyAnswer'))}</button>` +
      `<button class="link-button" data-copy="with-sources">${this.escaper.escape(t('app.copyWithSources'))}</button>` +
      `</span>`;
    for (const button of footer.querySelectorAll('[data-copy]')) {
      button.addEventListener('click', this.handleCopy.bind(this));
    }

    const sourcesSection = this.container.querySelector('.sources-section');
    if (sourcesSection) this.container.insertBefore(footer, sourcesSection);
    else this.container.appendChild(footer);
  }

  /** Copies the answer, optionally with its source list, to the clipboard. */
  async handleCopy(clickEvent) {
    const mode = clickEvent.currentTarget.dataset.copy;
    // The raw Markdown is copied rather than the rendered HTML, so the text
    // pastes usefully into Word, email or another Markdown editor alike.
    const text = mode === 'with-sources'
      ? this.buildAnswerWithSources()
      : this.accumulatedText;

    const copied = await this.clipboard.copy(text);
    this.toast.show(t(copied ? 'app.copiedToClipboard' : 'app.clipboardFailed'));
  }

  /** Returns the answer followed by a numbered list of its sources. */
  buildAnswerWithSources() {
    if (this.citations.length === 0) return this.accumulatedText;
    const lines = this.citations.map((citation) =>
      `[${citation.citation_number}] ${citation.document_name} — ${interfaceLanguage.sectionLabel(citation.section_label)}\n` +
      `    ${citation.relative_path}`);
    return `${this.accumulatedText}\n\n${t('app.sourcesHeading')}\n${lines.join('\n')}`;
  }
}

/* ------------------------------------------------------ conversation view */

/** Owns the question/answer thread and the streaming request lifecycle. */
class ConversationView {
  constructor(apiClient, toast, settingsReader, clipboard) {
    this.api = apiClient;
    this.toast = toast;
    this.settings = settingsReader;
    this.clipboard = clipboard || new ClipboardWriter();
    this.eventReader = new ServerSentEventReader();
    this.escaper = new HtmlEscaper();
    this.markdown = new MarkdownRenderer();
    this.citationLinker = new CitationLinker();
    this.formatter = new ValueFormatter();
    this.abortController = null;
    //: Follows the answer being generated; null while idle.
    this.streamFollower = null;
    //: Conversation currently being added to; null means nothing is saved.
    this.sessionId = null;
    //: Raised after a turn completes so the sidebar can refresh its list.
    this.onTurnCompleted = null;

    this.conversationElement = document.getElementById('conversation');
    this.inputElement = document.getElementById('input-question');
    this.sendButton = document.getElementById('button-send');
    this.stopButton = document.getElementById('button-stop');

    // The keyboard hint means nothing on a touch keyboard, and on a phone the
    // full placeholder wraps and is cut off inside the one-line box.
    if (window.matchMedia('(pointer: coarse)').matches) {
      this.inputElement.placeholder = t('app.questionPlaceholderShort');
    }

    //: The source-panel half of the bar; "Aturan negara" beside it stays visible.
    this.toolbarElement = document.getElementById('toolbar-sources');
    document.getElementById('button-expand-sources')
      .addEventListener('click', this.handleExpandAllSources.bind(this));
    document.getElementById('button-collapse-sources')
      .addEventListener('click', this.handleCollapseAllSources.bind(this));

    document.getElementById('composer')
      .addEventListener('submit', this.handleSubmit.bind(this));
    this.inputElement.addEventListener('keydown', this.handleKeyDown.bind(this));
    this.inputElement.addEventListener('input', this.handleInput.bind(this));
    this.stopButton.addEventListener('click', this.handleStop.bind(this));

    for (const example of document.querySelectorAll('.example')) {
      example.addEventListener('click', this.handleExampleClick.bind(this));
    }
  }

  /** Fills the composer from an example prompt and submits it. */
  handleExampleClick(clickEvent) {
    this.inputElement.value = clickEvent.currentTarget.textContent;
    this.handleSubmit(new Event('submit'));
  }

  /** Sends on Enter, inserts a newline on Shift+Enter. */
  handleKeyDown(keyEvent) {
    if (keyEvent.key === 'Enter' && !keyEvent.shiftKey) {
      keyEvent.preventDefault();
      this.handleSubmit(new Event('submit'));
    }
  }

  /** Grows the textarea to fit its content, up to the CSS max height. */
  handleInput() {
    this.inputElement.style.height = 'auto';
    this.inputElement.style.height = `${this.inputElement.scrollHeight}px`;
  }

  /**
   * Stops an in-flight answer.
   *
   * Closing the connection is not enough on its own: the server deliberately
   * keeps generating when a connection drops, so that a phone can resume. It
   * is therefore told explicitly that this answer is no longer wanted.
   */
  handleStop() {
    if (!this.abortController) return;
    const answerId = this.streamFollower ? this.streamFollower.answerId : null;
    if (answerId) this.api.cancelAnswer(answerId);
    this.abortController.abort();
  }

  /** Opens every source panel in the thread. */
  handleExpandAllSources() {
    this.#setAllSourcePanels(true);
  }

  /** Closes every source panel in the thread. */
  handleCollapseAllSources() {
    this.#setAllSourcePanels(false);
  }

  /** Opens or closes every source panel at once. */
  #setAllSourcePanels(shouldOpen) {
    for (const panel of this.conversationElement.querySelectorAll('details.sources-section')) {
      panel.open = shouldOpen;
    }
  }

  /**
   * Shows the toolbar only once a source panel exists to act on.
   *
   * Two buttons that do nothing are worse than no buttons: they invite a click
   * and teach the reader that the control is broken.
   */
  #refreshToolbarVisibility() {
    const hasPanels =
      this.conversationElement.querySelector('details.sources-section') !== null;
    this.toolbarElement.classList.toggle('hidden', !hasPanels);
  }

  /** Validates input, renders the question, then streams the answer. */
  async handleSubmit(submitEvent) {
    if (submitEvent) submitEvent.preventDefault();
    const question = this.inputElement.value.trim();
    if (!question || this.abortController) return;

    const welcome = this.conversationElement.querySelector('.welcome');
    if (welcome) welcome.remove();

    this.inputElement.value = '';
    this.inputElement.style.height = 'auto';
    this.#appendQuestion(question);

    const answerContainer = this.#appendAnswerContainer();
    const renderer = new AnswerRenderer(answerContainer, this.api, this.toast, this.clipboard);
    this.#setBusy(true);

    // A question asked with no session open starts one, so nothing is lost by
    // simply beginning to type rather than remembering to click "new".
    if (!this.sessionId) {
      try {
        const created = await this.api.createSession(null);
        this.sessionId = created.session_id;
      } catch {
        // Persistence is a convenience; answering must not depend on it.
      }
    }

    this.abortController = new AbortController();
    this.streamFollower = new AnswerStreamFollower(
      this.api, this.eventReader, this.#buildStreamCallbacks(renderer)
    );
    try {
      await this.streamFollower.follow(
        {
          question,
          folder_filter: this.settings.folderFilter() || null,
          backend_name: this.settings.backendName() || null,
          passage_count: this.settings.passageCount(),
          session_id: this.sessionId,
          language: interfaceLanguage.code,
        },
        this.abortController.signal
      );
    } catch (error) {
      if (error.name === 'AbortError') {
        renderer.renderError(t('app.stoppedByUser'));
      } else if (error instanceof AnswerStreamError) {
        renderer.renderError(error.message);
      } else {
        renderer.renderError(t('app.serverUnreachable', { error: error.message }));
      }
      renderer.finish({ elapsed_seconds: 0 });
    } finally {
      this.abortController = null;
      this.streamFollower = null;
      this.#setBusy(false);
      this.#scrollToBottom();
      this.#refreshToolbarVisibility();
      if (this.onTurnCompleted) this.onTurnCompleted(this.sessionId);
    }
  }

  /** Starts an empty conversation, discarding what is on screen. */
  startNewConversation(sessionId) {
    this.sessionId = sessionId || null;
    this.conversationElement.innerHTML =
      `<div class="welcome"><h2>${this.escaper.escape(t('app.newConversationTitle'))}</h2>` +
      `<p>${this.escaper.escape(t('app.newConversationBody'))}</p></div>`;
    this.#refreshToolbarVisibility();
  }

  /** Loads a saved conversation and redraws every stored turn. */
  async openSession(sessionId) {
    this.sessionId = sessionId;
    this.conversationElement.innerHTML = '';
    let session;
    try {
      session = await this.api.fetchSession(sessionId);
    } catch (error) {
      this.toast.show(t('app.loadConversationsFailed', { error: error.message }));
      return;
    }

    for (const turn of session.turns || []) {
      if (turn.role === 'user') this.#appendQuestion(turn.content);
      else this.#appendStoredAnswer(turn);
    }
    if ((session.turns || []).length === 0) this.startNewConversation(sessionId);
    this.#refreshToolbarVisibility();
    this.#scrollToBottom();
  }

  /** Redraws one stored answer, including its citations and copy actions. */
  #appendStoredAnswer(turn) {
    const container = this.#appendAnswerContainer();
    const renderer = new AnswerRenderer(container, this.api, this.toast, this.clipboard);
    // Replay through the same renderer the live stream uses, so a restored
    // answer is indistinguishable from a freshly generated one.
    renderer.handleEvent({ name: 'sources', payload: { citations: turn.citations || [] } });
    renderer.handleEvent({ name: 'token', payload: { text: turn.content } });
    renderer.handleEvent({
      name: 'done',
      payload: {
        elapsed_seconds: turn.elapsed_seconds,
        passage_count: (turn.citations || []).length,
        backend_name: turn.backend_name,
        model_name: turn.model_name,
      },
    });
  }

  /** Connects a stream follower's notifications to one answer's renderer. */
  #buildStreamCallbacks(renderer) {
    return {
      onEvent: this.#buildEventHandler(renderer),
      onReconnecting: renderer.showConnectionNotice.bind(renderer),
      onReconnected: renderer.clearConnectionNotice.bind(renderer),
    };
  }

  /** Returns a handler that forwards events and keeps the view scrolled. */
  #buildEventHandler(renderer) {
    const view = this;
    return function forwardEvent(event) {
      renderer.handleEvent(event);
      view.#scrollToBottom();
    };
  }

  /** Appends the user's question bubble, with its own copy action. */
  #appendQuestion(question) {
    const message = document.createElement('div');
    message.className = 'message question';
    message.innerHTML =
      `<button class="question-copy" title="${this.escaper.escape(t('app.copyQuestionTitle'))}">${this.escaper.escape(t('app.copy'))}</button>` +
      `<div class="question-bubble">${this.escaper.escape(question)}</div>`;

    const copyButton = message.querySelector('.question-copy');
    // Assigned as a property rather than through innerHTML, so the text needs
    // no escaping and cannot be mangled by quoting.
    copyButton.dataset.question = question;
    copyButton.addEventListener('click', this.handleCopyQuestion.bind(this));

    this.conversationElement.appendChild(message);
    this.#scrollToBottom();
  }

  /** Copies one question to the clipboard. */
  async handleCopyQuestion(clickEvent) {
    const copied = await this.clipboard.copy(clickEvent.currentTarget.dataset.question || '');
    this.toast.show(t(copied ? 'app.questionCopied' : 'app.clipboardFailed'));
  }

  /** Appends and returns the container an AnswerRenderer will fill. */
  #appendAnswerContainer() {
    const message = document.createElement('div');
    message.className = 'message answer';
    const block = document.createElement('div');
    block.className = 'answer-block';
    message.appendChild(block);
    this.conversationElement.appendChild(message);
    return block;
  }

  /** Toggles the send and stop buttons for the duration of a request. */
  #setBusy(isBusy) {
    this.sendButton.classList.toggle('hidden', isBusy);
    this.stopButton.classList.toggle('hidden', !isBusy);
    this.inputElement.disabled = isBusy;
  }

  /** Keeps the newest content in view during streaming. */
  #scrollToBottom() {
    this.conversationElement.scrollTop = this.conversationElement.scrollHeight;
  }
}

/* ------------------------------------------------------------ browse panel */

/** Retrieval-only search that shows passages and their raw scores. */
class BrowsePanel {
  constructor(apiClient, toast, settingsReader, clipboard) {
    this.api = apiClient;
    this.toast = toast;
    this.settings = settingsReader;
    this.clipboard = clipboard || new ClipboardWriter();
    this.escaper = new HtmlEscaper();
    document.getElementById('browse-form')
      .addEventListener('submit', this.handleSubmit.bind(this));
  }

  /** Runs a retrieval-only query and renders the passages. */
  async handleSubmit(submitEvent) {
    submitEvent.preventDefault();
    const question = document.getElementById('input-browse').value.trim();
    if (!question) return;

    const resultsElement = document.getElementById('browse-results');
    resultsElement.innerHTML = `<p class="empty-hint">${this.escaper.escape(t('app.searching'))}</p>`;

    try {
      const result = await this.api.search({
        question,
        folder_filter: this.settings.folderFilter() || null,
        passage_count: Math.max(10, this.settings.passageCount()),
      });
      this.#render(resultsElement, result.passages);
    } catch (error) {
      resultsElement.innerHTML = `<p class="empty-hint">${this.escaper.escape(t('app.searchFailed', { error: error.message }))}</p>`;
    }
  }

  /** Paints the passage list, or an empty-state message. */
  #render(resultsElement, passages) {
    if (!passages || passages.length === 0) {
      resultsElement.innerHTML = `<p class="empty-hint">${this.escaper.escape(t('app.noPassages'))}</p>`;
      return;
    }
    resultsElement.innerHTML = passages.map(this.#renderPassage.bind(this)).join('');

    // Exactly one copy button is rendered per passage, in the same order, so
    // the two lists line up index for index. The text is attached as a property
    // rather than an attribute, so it never has to survive HTML escaping.
    const buttons = resultsElement.querySelectorAll('.passage-copy');
    buttons.forEach((button, index) => {
      const passage = passages[index];
      if (!passage) return;
      button.dataset.passageText = passage.text || '';
      button.dataset.documentName = passage.document_name || '';
      button.dataset.sectionLabel = interfaceLanguage.sectionLabel(passage.section_label);
      button.dataset.relativePath = passage.relative_path || '';
      button.addEventListener('click', this.handleCopyPassage.bind(this));
    });
  }

  /** Copies one retrieved passage, with the document it came from. */
  async handleCopyPassage(clickEvent) {
    const button = clickEvent.currentTarget;
    const attributed =
      `"${button.dataset.passageText}"\n` +
      `— ${button.dataset.documentName} (${button.dataset.sectionLabel})\n` +
      `  ${button.dataset.relativePath}`;
    const copied = await this.clipboard.copy(attributed);
    this.toast.show(t(copied ? 'app.passageCopied' : 'app.copyFailed'));
  }

  /** Renders one passage card with its individual retrieval scores. */
  #renderPassage(passage) {
    const scoreParts = [];
    if (passage.dense_score !== null && passage.dense_score !== undefined) {
      scoreParts.push(`${t('app.semantic')} ${passage.dense_score.toFixed(3)}`);
    }
    if (passage.sparse_score !== null && passage.sparse_score !== undefined) {
      scoreParts.push(`${t('app.keyword')} ${passage.sparse_score.toFixed(2)}`);
    }
    if (passage.reranked_score !== null && passage.reranked_score !== undefined) {
      scoreParts.push(`rerank ${passage.reranked_score.toFixed(3)}`);
    }
    const ocrBadge = passage.produced_by_ocr ? '<span class="badge ocr">OCR</span>' : '';

    return `
      <div class="passage-item">
        <div class="source-card-header">
          <span class="source-number">${passage.citation_number}</span>
          <span class="source-name">${this.escaper.escape(passage.document_name)}</span>
          ${ocrBadge}
        </div>
        <div class="source-meta">${this.escaper.escape(passage.relative_path)} · ${this.escaper.escape(interfaceLanguage.sectionLabel(passage.section_label))}</div>
        <div class="source-excerpt">${this.escaper.escape(passage.text)}</div>
        <div class="passage-footer">
          <span class="passage-scores">${scoreParts.map((part) => this.escaper.escape(part)).join(' · ')}</span>
          <span class="passage-actions">
            <button class="link-button passage-copy">${this.escaper.escape(t('app.copyPassage'))}</button>
            <a class="link-button" download
               href="/api/documents/${encodeURIComponent(passage.document_id)}/download">${this.escaper.escape(t('app.download'))}</a>
          </span>
        </div>
      </div>`;
  }
}

/* --------------------------------------------------------- documents panel */

/** Lists indexed documents with their status and chunk counts. */
class DocumentsPanel {
  constructor(apiClient, formatter, toast) {
    this.api = apiClient;
    this.formatter = formatter;
    this.toast = toast;
    this.escaper = new HtmlEscaper();
    this.loadedDocuments = [];
    //: Reveal/open act on the server's desktop, so they are hidden from
    //: visitors arriving over the network until proven local.
    this.isLocalClient = false;

    document.getElementById('button-refresh-documents')
      .addEventListener('click', this.refresh.bind(this));
    document.getElementById('select-document-status')
      .addEventListener('change', this.refresh.bind(this));
    document.getElementById('input-document-filter')
      .addEventListener('input', this.renderRows.bind(this));
  }

  /** Fetches the document list for the selected status filter. */
  async refresh() {
    const statusFilter = document.getElementById('select-document-status').value;
    try {
      const result = await this.api.fetchDocuments(statusFilter);
      this.loadedDocuments = result.documents || [];
      this.renderRows();
    } catch (error) {
      this.toast.show(t('app.loadDocumentsFailed', { error: error.message }));
    }
  }

  /** Renders rows matching the free-text filter box. */
  renderRows() {
    const filterText = document.getElementById('input-document-filter').value.trim().toLowerCase();
    const tableBody = document.getElementById('document-rows');
    const visibleDocuments = filterText
      ? this.loadedDocuments.filter((document_) =>
          `${document_.display_name} ${document_.relative_path}`.toLowerCase().includes(filterText))
      : this.loadedDocuments;

    if (visibleDocuments.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="7" style="color:var(--text-muted);padding:22px 10px">${this.escaper.escape(t('app.noDocuments'))}</td></tr>`;
      return;
    }
    tableBody.innerHTML = visibleDocuments.map(this.#renderRow.bind(this)).join('');
    for (const button of tableBody.querySelectorAll('.link-button')) {
      button.addEventListener('click', this.handleReveal.bind(this));
    }
  }

  /** Renders one table row for a document. */
  #renderRow(documentRecord) {
    const statusLabels = {
      indexed: t('app.documentStatus.indexed'),
      empty: t('app.documentStatus.empty'),
      failed: t('app.documentStatus.failed'),
    };
    const errorLine = documentRecord.error_message
      ? `<div class="document-path-cell" style="color:var(--danger)">${this.escaper.escape(documentRecord.error_message)}</div>`
      : '';
    const scannedLine = documentRecord.scanned_section_count > 0
      ? `<div class="document-path-cell" style="color:var(--warning)">${this.escaper.escape(t('app.scannedPages', { count: documentRecord.scanned_section_count }))}</div>`
      : '';

    return `
      <tr>
        <td>
          <div class="document-name-cell">${this.escaper.escape(documentRecord.display_name)}</div>
          <div class="document-path-cell">${this.escaper.escape(documentRecord.relative_path)}</div>
          ${errorLine}${scannedLine}
        </td>
        <td>${this.escaper.escape(interfaceLanguage.countryName(documentRecord.top_level_folder))}</td>
        <td><span class="status-pill ${documentRecord.status}">${statusLabels[documentRecord.status] || documentRecord.status}</span></td>
        <td class="numeric">${this.formatter.fileSize(documentRecord.size_bytes)}</td>
        <td class="numeric">${this.formatter.count(documentRecord.chunk_count)}</td>
        <td class="numeric">${this.formatter.count(documentRecord.section_count)}</td>
        <td class="action-cell">
          <a class="link-button" download
             href="/api/documents/${encodeURIComponent(documentRecord.document_id)}/download">${this.escaper.escape(t('app.download'))}</a>
          ${this.isLocalClient
            ? `<button class="link-button" data-document-id="${this.escaper.escape(documentRecord.document_id)}">${this.escaper.escape(t('app.show'))}</button>`
            : ''}
        </td>
      </tr>`;
  }

  /** Records whether reveal/open are meaningful for this visitor. */
  setLocalClient(isLocal) {
    this.isLocalClient = isLocal;
  }

  /** Reveals the selected document in Explorer on the server machine. */
  async handleReveal(clickEvent) {
    try {
      await this.api.runFileAction(clickEvent.currentTarget.dataset.documentId, 'reveal');
    } catch (error) {
      this.toast.show(t('app.openFailed', { error: error.message }));
    }
  }
}

/* ------------------------------------------------------------------- tabs */

/** Switches between the ask, browse and documents panels. */
class TabController {
  constructor(onTabShown) {
    this.onTabShown = onTabShown;
    for (const tab of document.querySelectorAll('.tab')) {
      tab.addEventListener('click', this.handleClick.bind(this));
    }
  }

  /** Activates the clicked tab and its panel. */
  handleClick(clickEvent) {
    const tabName = clickEvent.currentTarget.dataset.tab;
    for (const tab of document.querySelectorAll('.tab')) {
      tab.classList.toggle('active', tab.dataset.tab === tabName);
    }
    for (const panel of document.querySelectorAll('.tab-panel')) {
      panel.classList.toggle('active', panel.id === `panel-${tabName}`);
    }
    if (this.onTabShown) this.onTabShown(tabName);
  }
}

/* ------------------------------------------------------------ application */

/** Reads the current values of the sidebar's retrieval controls. */
class RetrievalSettingsReader {
  /** The country whose rules the question is about: a top-level corpus folder. */
  folderFilter() {
    return document.getElementById('select-country').value;
  }

  /** The selected language-model backend identifier. */
  backendName() {
    return document.getElementById('select-backend').value;
  }

  /** How many passages to place in the answer context. */
  passageCount() {
    return Number(document.getElementById('input-passages').value);
  }
}

/**
 * "Aturan negara" as a list with flags.
 *
 * A native select cannot show images, so the choice is shown here instead.
 * The hidden select beside it still holds the value the rest of the page
 * reads, and every choice made here is announced on it as a change.
 */
class CountryPicker {
  constructor(select) {
    this.select = select;
    this.button = document.getElementById('country-button');
    this.list = document.getElementById('country-list');
    this.escaper = new HtmlEscaper();

    this.button.addEventListener('click', this.handleButtonClick.bind(this));
    this.button.addEventListener('keydown', this.handleButtonKeyDown.bind(this));
    this.list.addEventListener('keydown', this.handleListKeyDown.bind(this));
    this.list.addEventListener('click', this.handleListClick.bind(this));
    document.addEventListener('click', this.handleDocumentClick.bind(this));
    this.select.addEventListener('change', this.render.bind(this));
  }

  /** Redraws the button and the list from the select's options. */
  render() {
    const options = [...this.select.options];
    const chosen = options.find((option) => option.value === this.select.value);
    this.button.innerHTML = chosen
      ? `${this.#entry(chosen)}<span class="country-caret" aria-hidden="true">▾</span>`
      : '';
    this.list.innerHTML = options
      .map((option) =>
        `<li role="option" tabindex="-1" data-value="${this.escaper.escape(option.value)}"` +
        ` aria-selected="${option.value === this.select.value}">${this.#entry(option)}</li>`)
      .join('');
  }

  /** Opens or closes the list. */
  handleButtonClick(clickEvent) {
    clickEvent.stopPropagation();
    if (this.#isOpen()) this.close();
    else this.#open();
  }

  /** Arrow keys, Enter and Space open the list from the button. */
  handleButtonKeyDown(keyEvent) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(keyEvent.key)) {
      keyEvent.preventDefault();
      this.#open();
    }
  }

  /** Moves through the list, chooses, or closes it, from the keyboard. */
  handleListKeyDown(keyEvent) {
    const items = [...this.list.querySelectorAll('li')];
    const index = items.indexOf(document.activeElement);
    if (keyEvent.key === 'ArrowDown' || keyEvent.key === 'ArrowUp') {
      keyEvent.preventDefault();
      const step = keyEvent.key === 'ArrowDown' ? 1 : -1;
      items[(index + step + items.length) % items.length].focus();
    } else if (keyEvent.key === 'Enter' || keyEvent.key === ' ') {
      keyEvent.preventDefault();
      if (items[index]) this.#choose(items[index].dataset.value);
    } else if (keyEvent.key === 'Escape' || keyEvent.key === 'Tab') {
      this.close();
      if (keyEvent.key === 'Escape') this.button.focus();
    }
  }

  /** Chooses the clicked country. */
  handleListClick(clickEvent) {
    const item = clickEvent.target.closest('li');
    if (item) this.#choose(item.dataset.value);
  }

  /** A click anywhere else closes the list. */
  handleDocumentClick(clickEvent) {
    if (this.#isOpen() && !this.list.contains(clickEvent.target)) this.close();
  }

  /** Closes the list. */
  close() {
    this.list.classList.add('hidden');
    this.button.setAttribute('aria-expanded', 'false');
  }

  #open() {
    if (this.select.options.length === 0) return;
    this.list.classList.remove('hidden');
    this.button.setAttribute('aria-expanded', 'true');
    const selected = this.list.querySelector('[aria-selected="true"]') || this.list.querySelector('li');
    if (selected) selected.focus();
  }

  #isOpen() {
    return !this.list.classList.contains('hidden');
  }

  /** Sets the select, announces the change, and returns to the button. */
  #choose(country) {
    this.close();
    this.button.focus();
    if (country === this.select.value) return;
    this.select.value = country;
    this.select.dispatchEvent(new Event('change'));
  }

  /** One country as flag and name. */
  #entry(option) {
    return `<span class="country-flag" aria-hidden="true">${flagImage(option.value)}</span>` +
      `<span>${this.escaper.escape(option.textContent)}</span>`;
  }
}

/** Shows who is signed in, with which role, and offers signing out. */
class AccountBar {
  constructor(apiClient, toast) {
    this.api = apiClient;
    this.toast = toast;
    this.isAdmin = false;
    document.getElementById('button-logout')
      .addEventListener('click', this.handleSignOut.bind(this));
  }

  /** Reads the current session and paints the account bar. */
  async refresh() {
    let user;
    try {
      user = await this.api.fetchCurrentUser();
    } catch {
      return;
    }
    if (!user.signed_in) {
      SessionGuard.redirectToSignIn();
      return;
    }
    this.isAdmin = Boolean(user.is_admin);

    const displayName = user.username;
    document.getElementById('account-name').textContent = displayName;
    document.getElementById('account-avatar').textContent = displayName.charAt(0).toUpperCase();
    const roleBadge = document.getElementById('account-role');
    roleBadge.textContent = this.isAdmin ? 'Admin' : 'Guest';
    roleBadge.classList.toggle('admin', this.isAdmin);
    document.getElementById('account-bar').classList.toggle('is-admin', this.isAdmin);
  }

  /** Ends the session and returns to the landing page. */
  async handleSignOut() {
    try {
      await this.api.signOut();
    } catch (error) {
      this.toast.show(t('app.signOutFailed', { error: error.message }));
      return;
    }
    window.location.assign('/');
  }
}

/**
 * Hides the parts of the interface that only administrators use.
 *
 * Elements marked `data-admin-only` start hidden in the markup and are revealed
 * once the account is known to be an administrator, so a guest never sees
 * them appear and disappear while the page loads.
 */
class RoleBasedVisibility {
  /** Shows every administrator-only element to admins; hides it from guests. */
  apply(isAdmin) {
    for (const element of document.querySelectorAll('[data-admin-only]')) {
      element.classList.toggle('hidden', !isAdmin);
    }
  }
}

/**
 * The burger button: shows and hides the sidebar so the conversation and the
 * question box can use the whole screen.
 *
 * One control, two behaviours, split at the same width as the stylesheet. On a
 * wide screen the sidebar is a column that collapses away, and the choice is
 * remembered. On a phone it is a drawer over the conversation, closed by
 * default — left in the page it would push the question box half a screen down.
 */
class SidebarController {
  /** Must match the breakpoint in styles.css. */
  static NARROW_SCREEN_QUERY = '(max-width: 960px)';

  /** Browser-storage key for the wide-screen collapsed preference. */
  static STORAGE_KEY = 'exim-rag.sidebar-collapsed';

  constructor() {
    this.layout = document.getElementById('layout');
    this.toggleButton = document.getElementById('button-sidebar-toggle');
    this.narrowScreen = window.matchMedia(SidebarController.NARROW_SCREEN_QUERY);

    this.toggleButton.addEventListener('click', this.handleToggle.bind(this));
    document.getElementById('button-sidebar-close')
      .addEventListener('click', this.handleClose.bind(this));
    document.getElementById('sidebar-backdrop')
      .addEventListener('click', this.handleClose.bind(this));
    document.addEventListener('keydown', this.handleKeyDown.bind(this));
    this.narrowScreen.addEventListener('change', this.handleScreenWidthChange.bind(this));

    // The remembered state is applied without animation: sliding shut on every
    // page load would look like a glitch, not a preference. Reading
    // offsetWidth commits the state before transitions are switched back on.
    this.layout.classList.add('sidebar-instant');
    this.layout.classList.toggle('sidebar-collapsed', this.#readCollapsedPreference());
    void this.layout.offsetWidth;
    this.layout.classList.remove('sidebar-instant');
    this.#refreshToggleState();
  }

  /** Opens or closes the drawer on a phone; collapses or expands the column otherwise. */
  handleToggle() {
    if (this.narrowScreen.matches) {
      this.layout.classList.toggle('sidebar-open');
    } else {
      const isCollapsed = this.layout.classList.toggle('sidebar-collapsed');
      this.#writeCollapsedPreference(isCollapsed);
    }
    this.#refreshToggleState();
  }

  /** Closes the drawer from its own close button or the dimmed backdrop. */
  handleClose() {
    this.closeDrawer();
  }

  /** Escape closes an open drawer, as it would any overlay. */
  handleKeyDown(keyEvent) {
    if (keyEvent.key === 'Escape' && this.layout.classList.contains('sidebar-open')) {
      this.closeDrawer();
    }
  }

  /** A drawer left open while the window widens would cover nothing; drop it. */
  handleScreenWidthChange() {
    this.layout.classList.remove('sidebar-open');
    this.#refreshToggleState();
  }

  /**
   * Closes the drawer, if one is open.
   *
   * Called after picking a conversation on a phone: the reader chose it in
   * order to read it, and the drawer would otherwise still be covering it.
   */
  closeDrawer() {
    this.layout.classList.remove('sidebar-open');
    this.#refreshToggleState();
  }

  /** Mirrors whether the sidebar is showing into the button's label and state. */
  #refreshToggleState() {
    const isShowing = this.narrowScreen.matches
      ? this.layout.classList.contains('sidebar-open')
      : !this.layout.classList.contains('sidebar-collapsed');
    const label = t(isShowing ? 'app.hideSidebar' : 'app.showSidebar');
    this.toggleButton.setAttribute('aria-expanded', String(isShowing));
    this.toggleButton.setAttribute('aria-label', label);
    this.toggleButton.title = label;
  }

  /** The remembered wide-screen preference; storage can be unavailable. */
  #readCollapsedPreference() {
    try {
      return window.localStorage.getItem(SidebarController.STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  }

  /** Remembers the wide-screen preference where storage allows it. */
  #writeCollapsedPreference(isCollapsed) {
    try {
      window.localStorage.setItem(SidebarController.STORAGE_KEY, isCollapsed ? '1' : '0');
    } catch {
      // Private browsing or blocked storage: the toggle still works this visit.
    }
  }
}

/** Composes every component and starts the interface. */
class Application {
  constructor() {
    this.api = new ApiClient();
    this.formatter = new ValueFormatter();
    this.toast = new Toast(document.getElementById('toast'));
    this.settings = new RetrievalSettingsReader();

    this.clipboard = new ClipboardWriter();
    this.accountBar = new AccountBar(this.api, this.toast);
    this.roleVisibility = new RoleBasedVisibility();
    this.sidebar = new SidebarController();

    this.statusPanel = new StatusPanel(this.api, this.formatter, this.toast);
    this.ingestPanel = new IngestPanel(
      this.api, this.formatter, this.toast, this.handleIngestFinished.bind(this)
    );
    this.conversation = new ConversationView(
      this.api, this.toast, this.settings, this.clipboard
    );
    this.sessionPanel = new SessionPanel(
      this.api, this.formatter, this.toast, this.handleSessionOpened.bind(this)
    );
    this.conversation.onTurnCompleted = this.handleTurnCompleted.bind(this);
    this.browsePanel = new BrowsePanel(this.api, this.toast, this.settings, this.clipboard);
    this.documentsPanel = new DocumentsPanel(this.api, this.formatter, this.toast);
    this.tabs = new TabController(this.handleTabShown.bind(this));

    document.getElementById('select-backend')
      .addEventListener('change', this.statusPanel.renderSelectedBackendDetail.bind(this.statusPanel));
    document.getElementById('input-passages')
      .addEventListener('input', this.handlePassageCountChange.bind(this));
    document.getElementById('select-country')
      .addEventListener('change', this.handleCountryChange.bind(this));
    this.countryPicker = new CountryPicker(document.getElementById('select-country'));
  }

  /** Loads initial state and resumes any indexing run already in progress. */
  async start() {
    await this.accountBar.refresh();
    this.roleVisibility.apply(this.accountBar.isAdmin);
    this.ingestPanel.setAdministrator(this.accountBar.isAdmin);
    const status = await this.statusPanel.refresh();
    this.documentsPanel.setLocalClient(Boolean(status && status.client && status.client.is_local));
    await this.refreshCountries();
    await this.sessionPanel.refresh();
    const openSessionId = this.sessionPanel.rememberedOpenSession();
    if (openSessionId) {
      this.sessionPanel.setActiveSession(openSessionId);
      await this.conversation.openSession(openSessionId);
    }
    await this.ingestPanel.resumeIfRunning();
  }

  /** Opens a session chosen in the sidebar, or clears the view for a new one. */
  async handleSessionOpened(sessionId, isNew) {
    this.sidebar.closeDrawer();
    if (!sessionId) {
      this.conversation.startNewConversation(null);
      return;
    }
    if (isNew) this.conversation.startNewConversation(sessionId);
    else await this.conversation.openSession(sessionId);
  }

  /** Refreshes the sidebar after a turn so titles and times stay current. */
  async handleTurnCompleted(sessionId) {
    await this.sessionPanel.refresh();
    if (sessionId) this.sessionPanel.setActiveSession(sessionId);
  }

  /** Browser-storage key for the country chosen under "Aturan negara". */
  static COUNTRY_KEY = 'exim-rag.country';

  /** The country each interface language's readers most likely ask about. */
  static COUNTRY_OF_LANGUAGE = { id: 'Indonesia', vi: 'Vietnam' };

  /**
   * Fills "Aturan negara" with the countries in the index.
   *
   * One country at a time, never all: each question is restated in that
   * country's language and searched only in its documents, so rules of two
   * countries are never mixed in one answer. The reader's last choice is kept;
   * otherwise the country of the interface language, otherwise the first.
   */
  async refreshCountries() {
    let countries = [];
    try {
      countries = (await this.api.fetchFolders()).folders || [];
    } catch {
      /* An empty index has no countries yet. */
    }
    const select = document.getElementById('select-country');
    const previousChoice = select.value || this.#rememberedCountry();
    select.innerHTML = '';
    for (const country of countries) {
      const option = document.createElement('option');
      option.value = country;
      option.textContent = interfaceLanguage.countryName(country);
      select.appendChild(option);
    }
    const preferred = [previousChoice, Application.COUNTRY_OF_LANGUAGE[interfaceLanguage.code]];
    select.value = preferred.find((country) => countries.includes(country)) || countries[0] || '';
    this.countryPicker.render();
  }

  /** Remembers the chosen country for the next visit. */
  handleCountryChange() {
    try {
      window.localStorage.setItem(Application.COUNTRY_KEY, document.getElementById('select-country').value);
    } catch {
      // Blocked storage: the choice still holds for this visit.
    }
  }

  /** The country chosen on an earlier visit, if storage allows. */
  #rememberedCountry() {
    try {
      return window.localStorage.getItem(Application.COUNTRY_KEY) || '';
    } catch {
      return '';
    }
  }

  /** Refreshes status and the country list once an indexing run completes. */
  async handleIngestFinished() {
    await this.statusPanel.refresh();
    await this.refreshCountries();
  }

  /** Loads the document table the first time its tab is opened. */
  handleTabShown(tabName) {
    if (tabName === 'documents' && this.documentsPanel.loadedDocuments.length === 0) {
      this.documentsPanel.refresh();
    }
  }

  /** Mirrors the passage-count slider into its label. */
  handlePassageCountChange() {
    document.getElementById('passage-count-label').textContent =
      document.getElementById('input-passages').value;
  }
}

document.addEventListener('DOMContentLoaded', function bootstrapApplication() {
  interfaceLanguage.apply();
  interfaceLanguage.renderSwitchers();
  const application = new Application();
  application.start();
});
