import requests
import random
def get_proxy():
    """主要功能：获取代理信息"""
    Authkey = '39019B8B'
    Authpwd= '9B47BE1E7556'
    
    proxy_url='https://longterm.proxy.qg.net/query?key=39019B8B'
    
    response = requests.get(proxy_url).json()
    proxy_data_list=response['data']
    data=random.choice(proxy_data_list)
    proxy_ip=data['proxy_ip']
    proxy_host=data['server'].split(':')[1]
    
    
    proxies = {
        "http": f"http://{proxy_ip}:{proxy_host}",
        "https": f"http://{proxy_ip}:{proxy_host}",
    }
    proxy={"Authkey": Authkey, "Authpwd": Authpwd, "proxy_ip": proxy_ip,"proxy_host": proxy_host,"proxy": proxies}
    return proxy
    


print(get_proxy())



# import requests
# # 如果下面目标站不可用，请使用test.ipw.cn、ip.sb、ipinfo.io、ip-api.com、64.ipcheck.ing
# targetURL = "https://test.ipw.cn"
# proxyAddr = "125.124.110.252:18080"
# # authKey = "请改成您的Key"
# # password = "请改成您的AuthPwd"
# # 账密模式
# proxyUrl = "http://125.124.110.252:18080"

# proxies = {
#     "http": proxyUrl,
#     "https": proxyUrl,
# }
# resp = requests.get(targetURL, proxies=proxies)
# print(resp.text)
