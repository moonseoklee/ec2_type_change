### 어따쓰지?
- aws ec2 의 타입(인스턴스 스펙) 변경을 위해서는 인스턴스를 중지-> 타입변경 -> 인스턴스 시작 을 해야하는데, 필연적으로 downtime 이 발생한다. downtime 을 없애기 위해 aws autoscaling 작업을 표방하여 downtime 없는 스펙 변경을 위한 스크립트를 끄적였다.

### 절차 

    - 전제조건으로 ec2 는 autoscaling group 에 등록되어 있어야한다.
    
    - InstanceName 으로 실행 중인 인스턴스(이하 prevInstance)를 찾는다.

    - prevInstance 가 등록되어 있는 TargetGroup 을 찾는다.

    - prevInstance 의 이미지를 생성한다.

    - 생성된 이미지를 기반으로 새로운 인스턴스(이하 newInstance)를 생성한다.

    - newInstance 를 TargetGroup 에 등록하고 prevInstance를 TargetGroup 에서 제외한다.

    - prevInstance 를 중지 후 타입을 변경한 후 실행한다.

    - 실행 시 에러가 나게 되면 (가용한 인스턴스 타입이 없을 때) ec2Type을 사용자의 입력을 받아 재시도한다.

    - 변경된 타입의 prevInstance를 TargetGroup 에 등록한다.

    - newInstance 를 TargetGroup 에서 제외하고 중지한다.
    
