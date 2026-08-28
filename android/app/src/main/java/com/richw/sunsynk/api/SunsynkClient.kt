package com.richw.sunsynk.api

import android.util.Base64
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.spec.X509EncodedKeySpec
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher

class InvalidCredentialsException : Exception("Invalid username or password")

open class SunsynkClient(
    private val username: String,
    private val password: String,
    private val baseUrl: String = "https://api.sunsynk.net",
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private var accessToken: String? = null

    suspend fun login() = withContext(Dispatchers.IO) {
        val rawKey = fetchPublicKey()
        val encryptedPassword = rsaEncryptPkcs1v15(rawKey, password)
        val nonce = makeNonce()
        val sign = md5Hex("nonce=$nonce&source=sunsynk${rawKey.take(10)}")
        val payload = """
            {
              "username": "$username",
              "password": "$encryptedPassword",
              "grant_type": "password",
              "client_id": "csp-web",
              "source": "sunsynk",
              "nonce": $nonce,
              "sign": "$sign"
            }
        """.trimIndent()
        val resp = http.newCall(
            Request.Builder()
                .url("$baseUrl/oauth/token/new")
                .post(payload.toRequestBody("application/json".toMediaType()))
                .header("Content-Type", "application/json")
                .build()
        ).execute()
        val body = JsonParser.parseString(resp.body!!.string()).asJsonObject
        if (resp.isSuccessful && body.get("success").asBoolean) {
            accessToken = body.getAsJsonObject("data").get("access_token").asString
        } else {
            throw InvalidCredentialsException()
        }
    }

    protected suspend fun get(path: String, attempt: Int = 1): JsonObject = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("$baseUrl/$path")
            .header("Content-Type", "application/json")
            .apply { accessToken?.let { header("Authorization", "Bearer $it") } }
            .build()
        val resp = http.newCall(req).execute()
        if (resp.code == 401 && attempt == 1) {
            login()
            return@withContext get(path, attempt + 1)
        }
        JsonParser.parseString(resp.body!!.string()).asJsonObject
    }

    suspend fun getInverters(): List<JsonObject> {
        val body = get("api/v1/inverters?page=1&limit=10&total=0&status=-1&sn=&plantId=&type=-2&softVer=&hmiVer=&agentCompanyId=-1&gsn=")
        return body.getAsJsonObject("data").getAsJsonArray("infos")
            .map { it.asJsonObject }
    }

    private suspend fun fetchPublicKey(): String = withContext(Dispatchers.IO) {
        val nonce = makeNonce()
        val sign = md5Hex("nonce=$nonce&source=sunsynkPOWER_VIEW")
        val resp = http.newCall(
            Request.Builder()
                .url("$baseUrl/anonymous/publicKey?nonce=$nonce&source=sunsynk&sign=$sign")
                .header("Content-Type", "application/json")
                .build()
        ).execute()
        val body = JsonParser.parseString(resp.body!!.string()).asJsonObject
        if (!body.get("success").asBoolean) throw InvalidCredentialsException()
        body.get("data").asString
    }

    private fun makeNonce() = System.currentTimeMillis()

    private fun md5Hex(value: String): String {
        val md = MessageDigest.getInstance("MD5")
        return md.digest(value.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    private fun rsaEncryptPkcs1v15(rawKey: String, plaintext: String): String {
        val keyBytes = Base64.decode(rawKey, Base64.DEFAULT)
        val keySpec = X509EncodedKeySpec(keyBytes)
        val publicKey = KeyFactory.getInstance("RSA").generatePublic(keySpec)
        val cipher = Cipher.getInstance("RSA/ECB/PKCS1Padding")
        cipher.init(Cipher.ENCRYPT_MODE, publicKey)
        val encrypted = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(encrypted, Base64.NO_WRAP)
    }
}
