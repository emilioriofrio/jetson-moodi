from setuptools import find_packages, setup

package_name = 'camera'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='jetson',
    maintainer_email='jetson@todo.todo',
    description='TODO: Package description',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'camera_usb = camera.camera_usb:main',
            'camera_csi = camera.camera_csi:main',
            'simple_ar_usb = camera.simple_ar_usb:main',
            'simple_ar_csi = camera.simple_ar_csi:main',
        ],
    },
)
