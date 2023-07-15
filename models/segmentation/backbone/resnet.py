import math
import torch.nn as nn
import torch.utils.model_zoo as model_zoo
from ..sync_batchnorm.batchnorm import SynchronizedBatchNorm2d


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, dilation=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=stride,
                               dilation=dilation, padding=dilation, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, dilation=1, downsample=None, BatchNorm=None):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, bias=False)
        self.bn1 = BatchNorm(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=stride,
                               dilation=dilation, padding=dilation, bias=False)
        self.bn2 = BatchNorm(planes)
        self.conv3 = nn.Conv2d(planes, planes * 4, kernel_size=1, bias=False)
        self.bn3 = BatchNorm(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride
        self.dilation = dilation

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):

    def __init__(self,block, layers, output_stride, BatchNorm, mode, pretrained=True):
        self.inplanes = 64
        super(ResNet, self).__init__()
        blocks = [1, 2, 4]
        if output_stride == 32:
            strides = [1, 2, 2, 2]
            dilations = [1, 1, 1, 1]
        elif output_stride == 16:
            strides = [1, 2, 2, 1]
            # 为测试MCANet改的strides
            # strides = [1, 2, 1, 1]
            dilations = [1, 1, 1, 2]
            # 为测试MCANet改的dilations
            # dilations = [1, 1, 1, 1]
        elif output_stride == 8:
            strides = [1, 2, 1, 1]
            dilations = [1, 1, 2, 4]
        else:
            raise NotImplementedError

        if mode == 'sar':
            self.inchannel = 4
        elif mode == 'rgb':
            self.inchannel = 3

        # Modules
        # /2
        # self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3,
        #                         bias=False)
        # 为测试MCANet，stride改为4，原本为2
        # 为测试SEN12MS，stride改为1， 原本为2
        self.conv1_new = nn.Conv2d(self.inchannel, 64, kernel_size=7, stride=2, padding=3,
                                   bias=False)
        self.bn1 = BatchNorm(64)
        self.relu = nn.ReLU(inplace=True)
        # /4
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)

        self.layer1 = self._make_layer(
            block, 64, layers[0], stride=strides[0], dilation=dilations[0], BatchNorm=BatchNorm)

        # self.inplanes = 64

        # self.conv_rgb = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3,
        #                           bias=False)
        # self.bn_rgb = BatchNorm(64)
        # self.relu_rgb = nn.ReLU(inplace=True)
        # # /4
        # self.maxpool_rgb = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)

        # self.layer1_rgb = self._make_layer(
        #     block, 64, layers[0], stride=strides[0], dilation=dilations[0], BatchNorm=BatchNorm)

        # /8
        self.layer2 = self._make_layer(
            block, 128, layers[1], stride=strides[1], dilation=dilations[1], BatchNorm=BatchNorm)
        # /16
        self.layer3 = self._make_layer(
            block, 256, layers[2], stride=strides[2], dilation=dilations[2], BatchNorm=BatchNorm)
        # /32
        self.layer4 = self._make_MG_unit(
            block, 512, blocks=blocks, stride=strides[3], dilation=dilations[3], BatchNorm=BatchNorm)
        # self.layer4 = self._make_layer(block, 512, layers[3], stride=strides[3], dilation=dilations[3], BatchNorm=BatchNorm)
        self._init_weight()

        if pretrained:
            self._load_pretrained_model(layers)

    def _make_layer(self, block, planes, blocks, stride=1, dilation=1, BatchNorm=None):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion,
                          kernel_size=1, stride=stride, bias=False),
                BatchNorm(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride,
                            dilation, downsample, BatchNorm))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes,
                                dilation=dilation, BatchNorm=BatchNorm))

        return nn.Sequential(*layers)

    def _make_MG_unit(self, block, planes, blocks, stride=1, dilation=1, BatchNorm=None):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion,
                          kernel_size=1, stride=stride, bias=False),
                BatchNorm(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, dilation=blocks[0]*dilation,
                            downsample=downsample, BatchNorm=BatchNorm))
        self.inplanes = planes * block.expansion
        for i in range(1, len(blocks)):
            layers.append(block(self.inplanes, planes, stride=1,
                                dilation=blocks[i]*dilation, BatchNorm=BatchNorm))

        return nn.Sequential(*layers)

    def forward(self, input):
        # x = self.conv1(input)
        x = self.conv1_new(input)
        down1 = x
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)
        x = self.layer1(x)

        # rgb = self.conv_rgb(input2)
        # rgb = self.bn_rgb(rgb)
        # rgb = self.relu_rgb(rgb)
        # rgb = self.maxpool_rgb(rgb)
        # rgb = self.layer1_rgb(rgb)
        # x = x + rgb

        down2 = x
        x = self.layer2(x)
        down3 = x
        x = self.layer3(x)
        down4 = x
        x = self.layer4(x)
        down5 = x
        return down1, down2, down3, down4, down5

    def _init_weight(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, SynchronizedBatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

    def _load_pretrained_model(self, layers):
        if layers == [3, 4, 6, 3]:
            pretrain_dict = model_zoo.load_url(
                'https://download.pytorch.org/models/resnet50-19c8e357.pth')
        else:
            pretrain_dict = model_zoo.load_url(
                'https://download.pytorch.org/models/resnet101-5d3b4d8f.pth')
        # print(pretrain_dict.keys())
        model_dict = {}
        state_dict = self.state_dict()
        # print(state_dict.keys())
        for k, v in pretrain_dict.items():
            if k in state_dict:
                model_dict[k] = v
        state_dict.update(model_dict)
        self.load_state_dict(state_dict)


def ResNet101(output_stride, BatchNorm, mode, pretrained=True):
    """Constructs a ResNet-101 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(Bottleneck, [3, 4, 23, 3],
                   output_stride, BatchNorm, mode, pretrained=pretrained)
    return model


def ResNet50(output_stride, BatchNorm, mode='rgb', pretrained=True):
    """Constructs a ResNet-50 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(Bottleneck, [3, 4, 6, 3],
                   output_stride, BatchNorm, mode='rgb', pretrained=pretrained)
    return model


def ResNet34(output_stride, BatchNorm, mode, pretrained=True):
    """Constructs a ResNet-50 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(Bottleneck, [3, 4, 6, 3],
                   output_stride, BatchNorm, mode, pretrained=pretrained)
    return model


if __name__ == "__main__":
    import torch
    model = ResNet101(BatchNorm=nn.BatchNorm2d,
                      pretrained=True, output_stride=8)
    input = torch.rand(1, 3, 512, 512)
    output, low_level_feat = model(input)
    print(output.size())
    print(low_level_feat.size())