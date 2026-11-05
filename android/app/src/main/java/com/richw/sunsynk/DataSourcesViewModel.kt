package com.richw.sunsynk

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.google.gson.JsonParser
import com.richw.sunsynk.api.EonAuthException
import com.richw.sunsynk.api.EonClient
import com.richw.sunsynk.api.FuseAuthException
import com.richw.sunsynk.api.FuseClient
import com.richw.sunsynk.api.IndraAuthException
import com.richw.sunsynk.api.IndraClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class SignInStep { SIGNED_OUT, AWAITING_CONFIRMATION, SIGNED_IN }

data class SourceUi(
    val step: SignInStep = SignInStep.SIGNED_OUT,
    val busy: Boolean = false,
    val progress: String = "",
    val message: String = "",
    val error: String? = null,
    val summary: String = "",
)

/** Sign-in and download for the Fuse export meter (phone + SMS code) and the Indra EV
 *  charger (emailed link). Downloads are cached, so re-runs only fetch what is new. */
class DataSourcesViewModel(context: Context) : ViewModel() {
    private val store = DataSourceStore(context)

    private val _fuse = MutableStateFlow(SourceUi())
    val fuse = _fuse.asStateFlow()
    private val _indra = MutableStateFlow(SourceUi())
    val indra = _indra.asStateFlow()
    private val _eon = MutableStateFlow(SourceUi())
    val eon = _eon.asStateFlow()

    // The sign-in attempt in progress: its PKCE verifier and state (never stored).
    private var eonPkce: Triple<String, String, String>? = null      // verifier, state, url

    val fusePhone get() = store.fusePhone
    val indraEmail get() = store.indraSession.email

    init {
        _fuse.value = SourceUi(
            step = if (store.fuseCookies.containsKey("app-auth")) SignInStep.SIGNED_IN else SignInStep.SIGNED_OUT,
            summary = fuseSummary(),
        )
        val s = store.indraSession
        _indra.value = SourceUi(
            step = when {
                s.token.isNotEmpty() -> SignInStep.SIGNED_IN
                s.checkHash.isNotEmpty() -> SignInStep.AWAITING_CONFIRMATION
                else -> SignInStep.SIGNED_OUT
            },
            summary = indraSummary(),
        )
        _eon.value = SourceUi(
            step = if (store.eonTokens.refreshToken.isNotEmpty()) SignInStep.SIGNED_IN else SignInStep.SIGNED_OUT,
            summary = eonSummary(),
        )
    }

    private fun eonClient() = EonClient(store.eonDir, store.eonTokens, onTokens = { store.eonTokens = it })

    private fun eonSummary(): String {
        val parts = EonClient.KINDS.mapNotNull { kind ->
            val daily = runCatching { EonClient.loadDailyKwh(store.eonDir, kind) }.getOrDefault(emptyMap())
            if (daily.isEmpty()) null else "$kind ${"%,.0f".format(daily.values.sum())} kWh"
        }
        val days = runCatching { EonClient.loadDailyKwh(store.eonDir, "import") }.getOrDefault(emptyMap())
        if (parts.isEmpty()) return "No meter data downloaded yet"
        return "${days.keys.minOrNull()} to ${days.keys.maxOrNull()}: " + parts.joinToString(", ")
    }

    /** A fresh Auth0 sign-in URL (PKCE); the WebView loads it and hands back the redirect. */
    fun eonSignInUrl(): String {
        val (verifier, challenge) = EonClient.pkcePair()
        val state = java.util.UUID.randomUUID().toString()
        val url = EonClient.authorizeUrl(challenge, state)
        eonPkce = Triple(verifier, state, url)
        return url
    }

    /** The WebView (or a pasted URL) reached the E.ON app's callback: exchange the code. */
    fun eonFinish(callbackUrl: String) = run(_eon) {
        val (verifier, state, _) = eonPkce ?: throw EonAuthException("Start the sign-in first")
        val code = EonClient.parseCallback(callbackUrl, state)
        eonClient().exchangeCode(code, verifier)
        eonPkce = null
        _eon.value.copy(busy = false, step = SignInStep.SIGNED_IN, message = "Signed in to E.ON Next")
    }

    fun eonDownload() = run(_eon) {
        val client = eonClient()
        try {
            client.ensureToken()
        } catch (e: EonAuthException) {
            store.eonTokens = com.richw.sunsynk.api.EonTokens()
            return@run _eon.value.copy(busy = false, step = SignInStep.SIGNED_OUT,
                error = "E.ON session has expired — sign in again")
        }
        val months = client.collect { p -> _eon.value = _eon.value.copy(progress = p) }
        _eon.value = _eon.value.copy(progress = "Bills…")
        val charges = client.tariffs()
        _eon.value.copy(busy = false, progress = "", summary = eonSummary(),
            message = "${months.size} months (${client.fetched} requests, ${client.cached} months from cache), " +
                "${charges.size} bills")
    }

    private fun fuseClient() = FuseClient(store.fuseDir, store.fuseCookies, onCookies = { store.fuseCookies = it })
    private fun indraClient() = IndraClient(store.indraDir, store.indraSession, onSession = { store.indraSession = it })

    private fun fuseSummary(): String {
        val daily = runCatching { FuseClient.loadDailyExportKwh(store.fuseDir) }.getOrDefault(emptyMap())
        if (daily.isEmpty()) return "No meter data downloaded yet"
        return "${daily.size} days of meter export, ${daily.keys.first()} to ${daily.keys.last()}: " +
            "${"%.0f".format(daily.values.sum())} kWh"
    }

    private fun indraSummary(): String {
        val months = store.indraDir.listFiles()?.filter { Regex("month-\\d{4}-\\d{2}\\.json").matches(it.name) }
            ?.sortedBy { it.name } ?: emptyList()
        var kwh = 0.0
        var days = 0
        for (f in months) {
            val reports = runCatching {
                JsonParser.parseString(f.readText()).asJsonObject.getAsJsonArray("dailyReports")
            }.getOrNull() ?: continue
            for (r in reports) {
                kwh += r.asJsonObject.getAsJsonObject("totals")?.get("energyImportedKwh")?.asDouble ?: 0.0
                days++
            }
        }
        val sessions = store.indraDir.listFiles()?.count { it.name.startsWith("session-") } ?: 0
        if (days == 0) return "No charging data downloaded yet"
        return "$days charging days, $sessions sessions: ${"%.0f".format(kwh)} kWh charged"
    }

    private fun run(flow: MutableStateFlow<SourceUi>, block: suspend () -> SourceUi) {
        if (flow.value.busy) return
        flow.value = flow.value.copy(busy = true, error = null, message = "")
        viewModelScope.launch {
            flow.value = try {
                withContext(Dispatchers.IO) { block() }
            } catch (e: CancellationException) {
                throw e
            } catch (e: FuseAuthException) {
                flow.value.copy(busy = false, step = SignInStep.SIGNED_OUT, error = e.message, progress = "")
            } catch (e: EonAuthException) {
                flow.value.copy(busy = false, error = e.message, progress = "")
            } catch (e: IndraAuthException) {
                flow.value.copy(busy = false, error = e.message, progress = "")
            } catch (e: Exception) {
                flow.value.copy(busy = false, error = e.message ?: e.javaClass.simpleName, progress = "")
            }
        }
    }

    // ── Fuse ──────────────────────────────────────────────────────────────────

    // The SMS code is tied to the phoneSignIn cookies, so verify on the same client.
    private var pendingFuse: FuseClient? = null

    fun fuseSendCode(phone: String) = run(_fuse) {
        val number = FuseClient.normalisePhone(phone)
        store.fusePhone = number
        val client = FuseClient(store.fuseDir, onCookies = { store.fuseCookies = it })
        client.startSignIn(number)
        pendingFuse = client
        _fuse.value.copy(busy = false, step = SignInStep.AWAITING_CONFIRMATION, message = "Code sent by SMS to $number")
    }

    fun fuseVerify(code: String) = run(_fuse) {
        val client = pendingFuse ?: throw FuseAuthException("Send a code first")
        client.verifyOtp(code.trim())
        pendingFuse = null
        _fuse.value.copy(busy = false, step = SignInStep.SIGNED_IN, message = "Signed in to Fuse")
    }

    fun fuseDownload() = run(_fuse) {
        val client = fuseClient()
        if (!client.checkSession()) throw FuseAuthException("Fuse session has expired — sign in again")
        val premises = store.fusePremises.ifEmpty { client.premisesFid().also { store.fusePremises = it } }
        val months = client.collect(premises) { p -> _fuse.value = _fuse.value.copy(progress = p) }
        _fuse.value.copy(busy = false, progress = "", summary = fuseSummary(),
            message = "${months.size} months: ${client.fetched} downloaded, ${client.cached} from cache")
    }

    // ── Indra ─────────────────────────────────────────────────────────────────

    fun indraSendLink(email: String) = run(_indra) {
        indraClient().startSignIn(email.trim())
        _indra.value.copy(busy = false, step = SignInStep.AWAITING_CONFIRMATION,
            message = "Sign-in email sent to ${email.trim()}")
    }

    fun indraFinish(link: String) = run(_indra) {
        indraClient().finishSignIn(link)
        _indra.value.copy(busy = false, step = SignInStep.SIGNED_IN, message = "Signed in to Indra")
    }

    fun indraDownload() = run(_indra) {
        val client = indraClient()
        try {
            client.ensureToken()
        } catch (e: IndraAuthException) {
            store.indraSession = store.indraSession.copy(token = "")
            return@run _indra.value.copy(busy = false, step = SignInStep.SIGNED_OUT,
                error = "Indra session has expired — sign in again")
        }
        val months = client.collect { p -> _indra.value = _indra.value.copy(progress = p) }
        _indra.value.copy(busy = false, progress = "", summary = indraSummary(),
            message = "${months.size} months: ${client.fetched} downloaded, ${client.cached} from cache")
    }

    class Factory(private val context: Context) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            DataSourcesViewModel(context.applicationContext) as T
    }
}
