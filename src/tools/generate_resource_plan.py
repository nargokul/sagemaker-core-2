import json
import os
import pandas as pd

CLASS_METHODS = set(['create', 'add', 'start', 'register', 'import', 'list', 'get'])
OBJECT_METHODS = set(['refresh', 'delete', 'update', 'stop', 'deregister', 'wait', 'wait_for_status'])

'''
This class is used to extract the resources and its actions from the service-2.json file.
'''
class ResourceExtractor:

    # Wire additional methods to resources
    RESOURCE_TO_ADDITIONAL_METHODS = {
        'Cluster': ['DescribeClusterNode', 'ListClusterNodes'],
    }
    
    def __init__(self, service_json):
        self.service_json = service_json
        self.operations = self.service_json['operations']
        self.shapes = self.service_json['shapes']
        self.resource_actions = {}
        self.actions_under_resource = set()

        self._extract_resources_plan()
        
    
    def _filter_actions_for_resources(self, resources):
        for resource in sorted(resources, key=len, reverse=True):
            # filter action in actions
            filtered_actions = set([a for a in self.actions if a.endswith(resource) or (a.startswith('List') and a.endswith(resource +'s'))])
            self.actions_under_resource.update(filtered_actions)
            self.resource_actions[resource] = filtered_actions

            self.actions = self.actions - filtered_actions

    def _extract_resources_plan(self):
        self.actions = set(self.operations.keys())
        
        print(f"Total actions - {len(self.actions)}")
        self.create_resources = set([key[len('Create'):] for key in self.actions if key.startswith('Create')])
        self._filter_actions_for_resources(self.create_resources)

        self.add_resources = set([key[len('Add'):] for key in self.actions if key.startswith('Add')])
        self._filter_actions_for_resources(self.add_resources)

        self.start_resources = set([key[len('Start'):] for key in self.actions if key.startswith('Start')])
        self._filter_actions_for_resources(self.start_resources)

        self.register_resources = set([key[len('Register'):] for key in self.actions if key.startswith('Register')])
        self._filter_actions_for_resources(self.register_resources)

        self.import_resources = set([key[len('Import'):] for key in self.actions if key.startswith('Import')])
        self._filter_actions_for_resources(self.import_resources)

        self.resources = self.create_resources | self.add_resources | self.start_resources | self.register_resources | self.import_resources
        print(f"Total resource - {len(self.resources)}")

        print(f"Total actions_under_resource - {len(self.actions_under_resource)}")

        '''
        for resource, self.actions in self.resource_actions.items():
            print(f"{resource} -- {self.actions}")
        '''
        self._extract_resource_plan_as_dataframe()


    def _get_status_chain_and_states(self, shape_name, status_chain: list = None):
        if status_chain is None:
            status_chain = []
            
        member_data = self.shapes[shape_name]["members"]
        status_name = next((member for member in member_data if "status" in member.lower()), None)      
        if status_name is None:
            return [], []
        
        status_shape_name = member_data[status_name]["shape"]
        
        status_chain.append({"status": status_name, "status_shape": status_shape_name})
        
        # base case when shape has list of status enums
        if "enum" in self.shapes[status_shape_name]:
            resource_states = self.shapes[status_shape_name]["enum"]
            return status_chain, resource_states
        else:
            status_chain, resource_states = self._get_status_chain_and_states(status_shape_name, status_chain)       
            return status_chain , resource_states
        
    
    def _extract_resource_plan_as_dataframe(self):
        # built a dataframe for each resources and it has
        # resource_name, type, class_methods, object_methods, additional_methods and raw_actions
        self.df = pd.DataFrame(columns=['resource_name', 'type', 'class_methods', 
                                        'object_methods', 'chain_resource_name', 'additional_methods', 
                                        'raw_actions', 'resource_status_chain', 'resource_states'])

        for resource, actions in sorted(self.resource_actions.items()):
            class_methods = set()
            object_methods = set()
            additional_methods = set()
            chain_resource_names = set()
            resource_status_chain = set()
            resource_states = set()

            for action in actions:
                action_low = action.lower()
                resource_low = resource.lower()

                # describe action maps to get class method and refresh object method
                if action_low.split(resource_low)[0] == 'describe':
                    class_methods.add('get')
                    object_methods.add('refresh')
                    
                    # Find resource status chain and states if available
                    output_shape_name = self.operations[action]["output"]["shape"]
                    output_members_data = self.shapes[output_shape_name]["members"]
                    
                    # Edge case where a resource may have state but returns a single resource dict instead of a list of members
                    if len(output_members_data) == 1:
                        single_member_name = next(iter(output_members_data))
                        single_member_shape_name = output_members_data[single_member_name]["shape"]
                        resource_status_chain, resource_states = self._get_status_chain_and_states(single_member_shape_name)
                    else:
                        resource_status_chain, resource_states = self._get_status_chain_and_states(output_shape_name)
                    
                    # Determine whether resource needs a wait or wait_for_status method
                    if resource_low.endswith("job") or resource_low.endswith("jobv2"):
                        object_methods.add("wait")
                    elif any("inservice" in state.lower().replace("_", "") for state in resource_states):
                        object_methods.add("wait_for_status")
                        
                    continue            

                # Find chaining of resources
                if action_low.split(resource_low)[0] == 'create':
                    shape_name = self.operations[action]['input']['shape']
                    input = self.shapes[shape_name]
                    for member in input['members']:
                        if member.endswith('Name') or member.endswith('Names'):
                            chain_resource_name = member[:-len('Name')]

                            if chain_resource_name != resource and chain_resource_name in self.resources:
                                chain_resource_names.add(chain_resource_name)
                                #raise Exception(f"Chain Resource {chain_resource_names} for {resource} ")

                if action_low.split(resource_low)[0] in CLASS_METHODS:
                    class_methods.add(action_low.split(resource_low)[0])
                elif action_low.split(resource_low)[0] in OBJECT_METHODS:
                    object_methods.add(action_low.split(resource_low)[0])
                else:
                    additional_methods.add(action)

            # print(f"{resource} -- {sorted(class_methods)} -- {sorted(object_methods)} -- {sorted(chain_resource_names)} -- {sorted(additional_methods)} -- {sorted(actions)}")

            if resource in self.RESOURCE_TO_ADDITIONAL_METHODS:
                additional_methods.update(self.RESOURCE_TO_ADDITIONAL_METHODS[resource])

            new_row = pd.DataFrame({
                'resource_name': [resource],
                'type': ['resource'],
                'class_methods': [list(sorted(class_methods))],
                'object_methods': [list(sorted(object_methods))],
                'chain_resource_name': [list(sorted(chain_resource_names))],
                'additional_methods': [list(sorted(additional_methods))],
                'raw_actions': [list(sorted(actions))],
                'resource_status_chain': [list(resource_status_chain)],
                'resource_states': [list(resource_states)]
            })

            self.df = pd.concat([self.df, new_row], ignore_index=True)

        self.df.to_csv('resource_plan.csv', index=False)

    def get_resource_plan(self):
        return self.df
    


file_path = os.getcwd() + '/sample/sagemaker/2017-07-24/service-2.json'
with open(file_path, 'r') as file:
    data = json.load(file)
resource_extractor = ResourceExtractor(data)