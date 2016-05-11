import re
from Policy import Policy
from Address import Address
from AddressGroup import AddressGroup
from ServiceGroup import ServiceGroup
from Service import Service
from ipcalc import Network


class Search:

    def __init__(self, config):
        self.config = config

    def pad(self, s):
        s = s.ljust(len(s) + 1)
        s = s.rjust(len(s) + 1)
        return s

    def lpad(self, s):
        return s.rjust(len(s) + 1)

    def rpad(self, s):
        return s.ljust(len(s) + 1)

    def get_generic_service(self, lines):
        sprotocol = "N/A"
        sdestination_port = "0"
        for service_line in lines:
            if " protocol " in service_line:
                sprotocol = re.search('protocol (.*)', service_line)
                sprotocol = sprotocol.group(1)
            elif " destination-port " in service_line:
                sdestination_port = re.search('destination-port (.*)', service_line)
                sdestination_port = sdestination_port.group(1)
        return sprotocol, sdestination_port

    def get_junos_default_service(self, service):
        return self.get_generic_service(self.config.get_filtered_lines("junos-service", [service + " "], []))

    def parse_address(self, target):

        # base case - single address
        address_lines = self.config.get_filtered_lines("address", [self.pad(target)], ["description", "address-set"])
        if len(address_lines) > 0:
            target_address_line = address_lines[0]

            if "dns-name" in target_address_line:
                atype = "dns"
                target_address_value_search = re.search('address ' + target + ' dns-name (.*)',
                                                        target_address_line)
            else:
                atype = "ipv4"
                target_address_value_search = re.search('address ' + target + ' (.*)',
                                                        target_address_line)
            target_address_value = target_address_value_search.group(1)
            return Address(target, atype, target_address_value)
        else:
            # group
            group = AddressGroup(target)
            for group_target_address_line in self.config.get_filtered_lines("address", ["address-set", self.pad(target)],
                                                                       ["description"]):
                if group_target_address_line.find(self.pad(target)) == -1:
                    # bogus line - target is a member of a currently out of scope group
                    continue

                group_target_address = re.search(target + ' address(?:-set)? (.*)', group_target_address_line)
                group_target_address = group_target_address.group(1)

                # recurse
                address = self.parse_address(group_target_address)
                group.add_address(address)

            return group

    def search_by_ip(self, ip):

        address_objects = []
        search_objects = []
        policy_names = []

        if "\"" not in ip:
            # find networks containing the search
            for line in self.config.get_filtered_lines("address", ["/"], [ip, "/32", "description", "address-set"]):
                m = re.search('address (.*) (.*)', line)
                network = m.group(2)
                if m:
                    if ip in Network(network):
                        address_objects.append(m.group(1))
                        search_objects.append(m.group(1))
        else:
            ip = ip.strip("\"")

        if "/" not in ip:
            ip += "/32"

        for line in self.config.get_filtered_lines("address", [self.lpad(ip)], ["address-set"]):
            m = re.search('address (.+?) ', line)
            if m:
                address_objects.append(m.group(1))
                search_objects.append(m.group(1))

        for a in address_objects:
            for line in self.config.get_filtered_lines("address", [self.lpad(a)], ["global address "]):
                m = re.search('address-set (.+?) ', line)
                if m:
                    search_objects.append(m.group(1))

        for s in search_objects:
            for line in self.config.get_filtered_lines("policy", [self.lpad(s)], []):
                m = re.search('policy (.+?) ', line)
                if m and m.group(1) not in policy_names:
                    policy_names.append(m.group(1))

        return self.search(policy_names)

    def search(self, policy_names):

        policies = []

        for p in policy_names:
            src = []
            dest = []
            services = []
            action = None
            description = None
            src_zone = None
            dest_zone = None

            for policy_line in self.config.get_filtered_lines("policy", ["policy" + self.pad(p)], []):
                for address_type in ["source-address", "destination-address"]:
                    if address_type in policy_line:
                        target_address = re.search(address_type + ' (.*)', policy_line)
                        if target_address:
                            target_address = target_address.group(1)

                            if target_address == "any":
                                src.append(Address("any", "any", "any"))
                                continue  # "any" address, move on

                            if address_type == "source-address":
                                src.append(self.parse_address(target_address))
                            else:
                                dest.append(self.parse_address(target_address))

                #  services

                if "match application " in policy_line:
                    service = re.search('match application (.*)', policy_line)
                    service = service.group(1)

                    if service == "any":
                        service_objcet = Service("any", "any", "any")
                    elif "junos-" in service:
                        protocol, destination_port = self.get_junos_default_service(service)
                        service_objcet = Service(service, protocol, destination_port)
                    else:
                        stdout = self.config.get_filtered_lines("service", ["application-set", service], ["description"])
                        if len(stdout) != 0:
                            #  services in the set
                            service_objcet = ServiceGroup(service)
                            for service_set_line in stdout:
                                if " application " in service_set_line:
                                    service_set_service = re.search('application (.*)', service_set_line)
                                    service_set_service = service_set_service.group(1)
                                    stdout = self.config.get_filtered_lines("service", [service_set_service], ["application-set", "description"])
                                    if " term " not in service_set_line[0]:
                                        #  found the actual service
                                        protocol, destination_port = self.get_generic_service(stdout)
                                        service_objcet.add_service(Service(service_set_service, protocol, destination_port))
                                    else:
                                        #  termed service object
                                        terms = set()
                                        for lines in stdout:
                                            group_term = re.search(' term (.+?) ', service_set_line)
                                            group_term = group_term.group(1)
                                            terms.add(group_term)
                                        for term in terms:
                                            protocol, destination_port = self.get_generic_service(self.config.get_filtered_lines("service", [service_set_service, term], ["application-set", "description"]))
                                            service_objcet.add_service(Service(term, protocol, destination_port))

                        else:
                            protocol, destination_port = self.get_generic_service(self.config.get_filtered_lines("service", [service], ["application-set", "description"]))
                            service_objcet = Service(service, protocol, destination_port)

                    services.append(service_objcet)

                #  action

                if action is None:
                    if " then " in policy_line and " log " not in policy_line:
                        action = re.search('then (.+?)[^ ]+', policy_line)
                        action = action.group(1)

                #  description

                if " description " in policy_line:
                    description = re.search('description (.*)', policy_line)
                    description = description.group(1)
                    description = description.strip('"')

                # zones

                if src_zone is None:
                    if "global policy" in policy_line:
                        src_zone = dest_zone = "global"
                    else:
                        src_zone = re.search('from-zone (.+?) ', policy_line)
                        src_zone = src_zone.group(1)
                if dest_zone is None:
                    dest_zone = re.search('to-zone (.+?) ', policy_line)
                    dest_zone = dest_zone.group(1)

            if description is None:
                description = ""
            policies.append(Policy(p, description, src, dest, action, services, src_zone, dest_zone))

        return policies
