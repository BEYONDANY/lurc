package com.beisen.models;

import com.google.gson.Gson;

/**
 * Auto-generated: 2018-04-02 17:10:58
 *
 * @author bejson.com (i@bejson.com)
 * @website http://www.bejson.com/java2pojo/
 */
public class Jwt_header {
    public Jwt_header(String alg, String kid) {
        setAlg(alg);
        setKid(kid);
    }

    private String alg;
    private String kid;

    public void setAlg(String alg) {
        this.alg = alg;
    }

    public String getAlg() {
        return alg;
    }

    public void setKid(String kid) {
        this.kid = kid;
    }

    public String getKid() {
        return kid;
    }

    @Override
    public String toString() {
        Gson gson = new Gson();
        return gson.toJson(this);
    }
}