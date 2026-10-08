#!/bin/sh
set -eu
# DROP installed before any workload exists. No general loopback rule: resolver
# 127.0.0.11, DNS 53 and all host/gateway routes are intentionally denied.
ip6tables -P INPUT DROP
ip6tables -P OUTPUT DROP
ip6tables -P FORWARD DROP
iptables -P INPUT DROP
iptables -P OUTPUT DROP
iptables -P FORWARD DROP
iptables -A INPUT -i lo -p tcp -m conntrack --ctstate ESTABLISHED -j ACCEPT
iptables -A INPUT -i lo -d 127.0.0.1 -p tcp -m multiport --dports 3306,8000 -j ACCEPT
iptables -A OUTPUT -o lo -p tcp -m conntrack --ctstate ESTABLISHED -j ACCEPT
iptables -A OUTPUT -o lo -d 127.0.0.1 -p tcp -m multiport --dports 3306,8000 -j ACCEPT
iptables -A OUTPUT -j REJECT
ip6tables -A OUTPUT -j REJECT
iptables-save > /tmp/ipv4.rules
ip6tables-save > /tmp/ipv6.rules
# The waiting process retains no capability. Only trusted daemon exec can inspect
# policy; untrusted containers have separate PID namespaces and ALL caps dropped.
exec setpriv --bounding-set=-all --inh-caps=-all --ambient-caps=-all --reuid=65534 --regid=65534 --clear-groups /bin/sh -c 'touch /tmp/guard-ready; printf "M4_GUARD_READY\n"; exec sleep infinity'
