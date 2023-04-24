import boto3
from datetime import datetime


class InstanceManger:
    def __init__(self, instance_name):
        self.__instance_name = instance_name
        self.__previous_instance_dict = None
        self.__image_id = None
        self.__new_instance_id = None
        self.ec2_client = boto3.client('ec2')
        self.elb_client = boto3.client('elbv2')
        self.target_group_arn = None

    @property
    def image_id(self):
        if not self.__image_id:
            raise ValueError('ImageId not exists')
        return self.__image_id

    @image_id.setter
    def image_id(self, image_id):
        self.__image_id = image_id

    @property
    def new_instance_id(self):
        if not self.__new_instance_id:
            raise ValueError('new_instance_id not exists')
        return self.__new_instance_id

    @new_instance_id.setter
    def new_instance_id(self, new_instance_id):
        self.__new_instance_id = new_instance_id

    @property
    def previous_instance_dict(self):
        if not self.__previous_instance_dict:
            raise ValueError('instance_dict not exists')
        return self.__previous_instance_dict

    @previous_instance_dict.setter
    def previous_instance_dict(self, previous_instance_dict):
        self.__previous_instance_dict = previous_instance_dict

    def get_volume_size(self):
        response = self.ec2_client.describe_volumes(
            VolumeIds=[
                self.previous_instance_dict['BlockDeviceMappings'][0]['Ebs']['VolumeId']
            ],
        )

        if len(response['Volumes']) != 1:
            raise
        return response['Volumes'][0]['Size']

    def describe_instance(self):

        response = self.ec2_client.describe_instances(
            Filters=[
                {
                    'Name': 'tag:Name',
                    'Values': [self.__instance_name]
                },
                {
                    'Name': 'instance-state-name',
                    'Values': ['running']
                }
            ]
        )

        if len(response['Reservations']) != 1:
            return False
        if len(response['Reservations'][0]['Instances']) != 1:
            return False

        instance = response['Reservations'][0]['Instances'][0]

        if not instance:
            raise ValueError('실행 중인 인스턴스가 유일하지 않습니다.')

        self.previous_instance_dict = instance

    def wait_until_image_created(self):
        waiter = self.ec2_client.get_waiter('image_available')
        waiter.wait(
            ImageIds=[
                self.image_id
            ],
            WaiterConfig={
                'Delay': 15,
                'MaxAttempts': 40
            }
        )

    def create_image(self):

        name_value = None
        if not self.previous_instance_dict:
            raise ValueError("발견된 인스턴스가 없습니다. 인스턴스를 먼저 발견해주세요")

        for d in self.previous_instance_dict['Tags']:
            if 'Key' in d and d['Key'] == 'Name':
                name_value = d['Value']
        if not name_value:
            raise

        response = self.ec2_client.create_image(
            Description='string',
            InstanceId=self.previous_instance_dict['InstanceId'],
            Name=f"{name_value}_{str(datetime.now().strftime('%Y%m%d%H%M'))}",
            NoReboot=True,
        )
        self.image_id = response['ImageId']
        self.wait_until_image_created()
        image = self.ec2_client.describe_images(ImageIds=[self.image_id])
        if image['Images'][0]['State'] == 'available':
            return True
        return False

    def start_instance_using_created_image(self, instance_type):

        response = self.ec2_client.run_instances(
            BlockDeviceMappings=[
                {
                    'DeviceName': '/dev/sda1',
                    'Ebs': {
                        'DeleteOnTermination': True,
                        'VolumeSize': self.get_volume_size(),
                        'VolumeType': '{volumetype}',
                    },
                },
            ],
            ImageId=self.image_id,
            InstanceType=instance_type,
            KeyName=self.previous_instance_dict['KeyName'],
            MaxCount=1,
            MinCount=1,
            SecurityGroupIds=[
                'something',
            ],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [{'Key': 'Name', 'Value': self.__instance_name}],
                },
            ],
        )

        if len(response['Instances']) != 1:
            raise

        self.new_instance_id = response['Instances'][0]['InstanceId']

        waiter = self.ec2_client.get_waiter('instance_running')
        waiter.wait(
            InstanceIds=[
                self.new_instance_id
            ],
            WaiterConfig={
                'Delay': 15,
                'MaxAttempts': 40
            }
        )

    def find_target_group_that_instance_registered(self):

        response = self.elb_client.describe_target_groups()

        ec2_registered_target_group_arns = []
        for target_group in response['TargetGroups']:
            response = self.elb_client.describe_target_health(
                TargetGroupArn=target_group['TargetGroupArn']
            )
            for target in response['TargetHealthDescriptions']:
                if self.previous_instance_dict['InstanceId'] == target['Target']['Id'] and target['TargetHealth'][
                    'State'] == 'healthy':
                    ec2_registered_target_group_arns.append(target_group['TargetGroupArn'])

        if len(ec2_registered_target_group_arns) != 1:
            raise ValueError(f'기존 인스턴스가 {len(ec2_registered_target_group_arns)}개의 타겟 그룹에 등록되어있습니다.')

        return ec2_registered_target_group_arns[0]

    def register_to_target_group_and_deregister_existed_instance_from_target_group(self):

        self.target_group_arn = self.find_target_group_that_instance_registered()

        response = self.elb_client.register_targets(
            TargetGroupArn=self.target_group_arn,
            Targets=[
                {
                    'Id': self.new_instance_id,
                    'Port': 80,
                },
            ]
        )

        waiter = self.elb_client.get_waiter('target_in_service')
        waiter.wait(TargetGroupArn=self.target_group_arn, Targets=[{'Id': self.new_instance_id}])

        response = self.elb_client.deregister_targets(
            TargetGroupArn=self.target_group_arn,
            Targets=[
                {
                    'Id': self.previous_instance_dict['InstanceId'],
                    'Port': 80,
                },
            ]
        )
        waiter = self.elb_client.get_waiter('target_deregistered')
        waiter.wait(TargetGroupArn=self.target_group_arn, Targets=[{'Id': self.previous_instance_dict['InstanceId']}])

    def change_previous_instance_type_and_register_target_group(self, ec2_type):

        previous_instance_id = self.previous_instance_dict['InstanceId']
        self.ec2_client.stop_instances(InstanceIds=[previous_instance_id])
        waiter = self.ec2_client.get_waiter('instance_stopped')
        waiter.wait(InstanceIds=[previous_instance_id])

        while True:
            try:
                self.ec2_client.modify_instance_attribute(InstanceId=previous_instance_id, Attribute='instanceType',
                                                          Value=ec2_type)

                self.ec2_client.start_instances(InstanceIds=[previous_instance_id])
                break
            except Exception:
                ec2_type = input('인스턴스 타입 입력 : ')

        waiter = self.ec2_client.get_waiter('instance_running')
        waiter.wait(InstanceIds=[previous_instance_id])

        response = self.elb_client.register_targets(
            TargetGroupArn=self.target_group_arn,
            Targets=[
                {
                    'Id': previous_instance_id,
                    'Port': 80,
                },
            ]
        )

        waiter = self.elb_client.get_waiter('target_in_service')
        waiter.wait(TargetGroupArn=self.target_group_arn, Targets=[{'Id': previous_instance_id}])

        response = self.elb_client.deregister_targets(
            TargetGroupArn=self.target_group_arn,
            Targets=[
                {
                    'Id': self.new_instance_id,
                    'Port': 80,
                },
            ]
        )
        waiter = self.elb_client.get_waiter('target_deregistered')
        waiter.wait(TargetGroupArn=self.target_group_arn, Targets=[{'Id': self.new_instance_id}])

        self.ec2_client.stop_instances(InstanceIds=[self.new_instance_id])
        waiter = self.ec2_client.get_waiter('instance_stopped')
        waiter.wait(InstanceIds=[self.new_instance_id])


def procedure(instance_name: str, target_type: str):
    im = InstanceManger(instance_name=instance_name)
    # 실행중인 인스턴스를 instance_name 으로 찾는다
    im.describe_instance()
    # 해당 인스턴스가 등록된 타겟 그룹을 찾는다
    im.find_target_group_that_instance_registered()
    # 인스턴스의 이미지를 생성한다.
    im.create_image()
    # 이미지를 기반으로 인스턴스를 생성한다.
    im.start_instance_using_created_image(instance_type='t2.micro')
    # 새로 생성한 인스턴스를 타겟 그룹에 등록하고 기존에 등록된 예전 인스턴스를 타겟그룹에서 제거한다.
    im.register_to_target_group_and_deregister_existed_instance_from_target_group()
    # 기존 인스턴스를 중지 후 타입을 변경 및 시작 한 뒤 타겟 그룹에 등록한다.
    im.change_previous_instance_type_and_register_target_group(ec2_type=target_type)



