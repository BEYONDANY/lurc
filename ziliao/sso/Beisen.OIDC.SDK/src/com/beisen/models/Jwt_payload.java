package com.beisen.models;

import com.google.gson.Gson;

import java.util.HashMap;

public class Jwt_payload {

    public Jwt_payload(String iss, String sub, String aud, Long exp, Long iat, HashMap<String, Object> cls) {
        setIss(iss);
        setSub(sub);
        setAud(aud);
        setExp(String.valueOf(exp));
        setIat(String.valueOf(iat));
        setCls(cls);
    }

    private String iss;
    private String sub;
    private String aud;
    private String exp;
    private String iat;
    private HashMap<String, Object> cls;


    public void setIss(String iss) {
        this.iss = iss;
    }

    public String getIss() {
        return iss;
    }

    public void setSub(String sub) {
        this.sub = sub;
    }

    public String getSub() {
        return sub;
    }

    public void setAud(String aud) {
        this.aud = aud;
    }

    public String getAud() {
        return aud;
    }

    public void setExp(String exp) {
        this.exp = exp;
    }

    public String getExp() {
        return exp;
    }

    public void setIat(String iat) {
        this.iat = iat;
    }

    public String getIat() {
        return iat;
    }

    public HashMap<String, Object> getCls() {
        return cls;
    }

    public void setCls(HashMap<String, Object> cls) {
        this.cls = cls;
    }

    @Override
    public String toString() {
        Gson gson = new Gson();
        return gson.toJson(this);
    }
}
