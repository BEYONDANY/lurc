package com.beisen;

import com.beisen.crypto.Crypto;
import com.beisen.models.Jwt;
import com.beisen.models.Jwt_payload;
import com.beisen.provider.BeisenTokenProvider;
import com.beisen.utility.FileUtils;
import com.beisen.utility.SafeTools;

import java.util.HashMap;

public class Main {

    public static void main(String[] args) {
        // demo参数为北森测试租户，直接运行复制生成的url，在浏览器访问即可
        try {
            String iss="oa.test.com";//iss,自行替换为请求的门户地址
            String aud="107934";//租户id,自行替换为客户租户id
            String sub="beixiaosen@beisen.com";//用户邮箱,自行替换为客户租户邮箱
            String header = BeisenTokenProvider.GetHeader("RS256", public_key);
            HashMap<String, Object> cls = new HashMap<String, Object>();// 用户自定义可见权限内容列表
            cls.put("appid", "100");
            cls.put("uty", "email");
            cls.put("url_type", "0");
            cls.put("isv_type", "0");
            cls.put("vsn", "1");
            long iat = SafeTools.getNowTimeStamp();// Issued At
//             Time：必须。JWT的构建的时间【Unix时间】。
            long exp = iat + 15 * 60; // Expiration time：必须。过期时间，超过此时间的ID
            String payload = BeisenTokenProvider.GetPayload(iss, sub, aud, exp, iat, cls);
            String token = BeisenTokenProvider.GetIdToken(header, payload, private_key);
            String url = "https://oapi.italent.cn/SSO/AuthCenter?id_token=" + token;
            System.out.println("单点URL：" + url);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
    //北森测试租户公钥私钥，请自行替换为客户公钥私钥
    public static final String public_key = "-----BEGIN PUBLIC KEY-----\n" +
            "MIIBIDANBgkqhkiG9w0BAQEFAAOCAQ0AMIIBCAKCAQEAm62ony17H8iumgcI6+b2\n" +
            "eDSS2cWdEuqIhTff+1P1CC6Ih4ViXIjZgNTPOqPB/ihv6fRW9J2q4GW/rAbmqtvN\n" +
            "kUQaNVNE2AA50umW97AGUlVgQNKrJW8/1lUAKJYrFU2HpyuuS58fQ2WmsQFpcpE3\n" +
            "S2nuueoVy18M1Gi28iV654zWNhaw+OnRVRS2h0AyDQbNOM6PnprIQn6Thdcr+79I\n" +
            "o77LdhS43qM9XiaTQAL0RPb38HneIu29A58ovuJzQ9s5kOUpDT9NCq+rPAmqj46Q\n" +
            "UCkqR1NOweUj/Q4auYEFEmYwbdSVqBaEWqrHmvbuhSMK0EVLLKKIhrHZJDcE3Eef\n" +
            "awIBAw==\n" +
            "-----END PUBLIC KEY-----\n";
    public static final String private_key = "-----BEGIN RSA PRIVATE KEY-----\n" +
            "MIIEoQIBAAKCAQEAm62ony17H8iumgcI6+b2eDSS2cWdEuqIhTff+1P1CC6Ih4Vi\n" +
            "XIjZgNTPOqPB/ihv6fRW9J2q4GW/rAbmqtvNkUQaNVNE2AA50umW97AGUlVgQNKr\n" +
            "JW8/1lUAKJYrFU2HpyuuS58fQ2WmsQFpcpE3S2nuueoVy18M1Gi28iV654zWNhaw\n" +
            "+OnRVRS2h0AyDQbNOM6PnprIQn6Thdcr+79Io77LdhS43qM9XiaTQAL0RPb38Hne\n" +
            "Iu29A58ovuJzQ9s5kOUpDT9NCq+rPAmqj46QUCkqR1NOweUj/Q4auYEFEmYwbdSV\n" +
            "qBaEWqrHmvbuhSMK0EVLLKKIhrHZJDcE3EefawIBAwKCAQAZ8kbFMj8v9sfEVoF8\n" +
            "pn5pXhh5oO+DJxbA3qVUjf4sB8Fr65BkwXmVeM00cKBVBr1RqLkoxPHQEPVHVnvH\n" +
            "JKJC4K8I4zYkAAmjJu5+nVZjDjq1eHHbkoqjuNVcGQcuN5ab3J0MmoU15kZy1ZGT\n" +
            "GDPh5v0e/Fj3OoIjZskoW5R77IsilbraWwEnbjCmBveu/OIWDShrL8Z/Vven6A7a\n" +
            "8noQB7TMhGvZ5sZrthkLW3ZU6a++3KqVJqRJJDtSDg1jMuOxysX7PAfCR/aa4ycp\n" +
            "kBMRqOUeV2PofhyeXDwOU4EzuOsYMc+G5uD80U8cWiRUMOG7qhG2bnTZq2kzcAa3\n" +
            "FzczAoGBAOZ/yku87hVJUhhlA0t+j5p2RxLFlI96C5aBaqZfU5YLvHYG3xcxnGDC\n" +
            "hJhzJpQi06LHQ0Wfi9t82djfMiqe7IKdkfcjJs3YaGAaxjW+NiHYQeulffn5eBHF\n" +
            "h4OG9AdTJS/Zc6n6mZZmwRFKIKUN2RWMj1k/cX7Aq2iq8HX9HzAjAoGBAKzmygQd\n" +
            "2bUfLXo+Exh0ieY+OIpV55HMaS8U/Cx6vNY31BikGxqRnjYVXWSIt5okqTawCpkE\n" +
            "egfP6+vzW/hRvWDIko4GsFCkl4N/8InX9/wN8d/ryP1VeGaBXS7cl/aI634DNg6E\n" +
            "E6gHAdumuW9+JKrduYemmkysAflEpmaUnSQZAoGBAJmqht0onrjbjBBDV4eptRGk\n" +
            "L2HZDbT8B7mrnG7qN7ldKE6vP2TLvZXXAxBMxGLB4myE14O/spJTO+XqIXG/SFcT\n" +
            "tqTCGd6QRZVnLs5+zsE61p0Y/qamUAvZBQJZ+ATiGMqQ98anEQ7vK2DcFcNekLkI\n" +
            "X5DU9lSAckXHSvlTaiAXAoGAc0SGrWk7zhTI/CliEE2xRCl7BuPvtohGH2NSyFHT\n" +
            "OXqNZcK8vGEUJA4+QwXPvBhwzyAHEK2mr9/ynUzn+uEo6zBhtAR1ixhlAlVLBo/6\n" +
            "qAlL6p0wqOOlmauTdJMP+bCc/qzOtFgNGq9Wkm8mSlQYcekmWm8RiHKr+4MZmbho\n" +
            "wrsCgYBeGJtu9FFHweRPEKgyUkI6PScL5L6KNi34eV+iPdqBFg1oH3oZrtQl7Dkk\n" +
            "YsKRpvTwwLr0HWb8ORGIWJWHENc3wlRoNIqW0+6ZLUAnvt+cRamDG91Y4idlKJcG\n" +
            "bFCzSN9WO5crDli4FUhH373johGJlW5ACDH7HwDm3Wpy9ksRpw==\n" +
            "-----END RSA PRIVATE KEY-----\n";
}
