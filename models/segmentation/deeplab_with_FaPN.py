import torch
import torch.nn as nn
import torch.nn.functional as F
from sar_project.models.segmentation.sync_batchnorm.batchnorm import SynchronizedBatchNorm2d
from sar_project.models.segmentation.backbone import build_backbone
from sar_project.models.segmentation.deform_conv_v2 import DeformConv2d
from sar_project.models.segmentation.domain_align_FaPN import DomainAlign_FaPN
from sar_project.models.segmentation.contrastive_module import Contrast_RS


class DeepLab_FaPN(nn.Module):
    def __init__(self, mode='sar', isExtract=True, backbone='resnet-101', output_stride=16, num_classes=2,
                 sync_bn=True, freeze_bn=False):
        super(DeepLab_FaPN, self).__init__()

        if sync_bn == True:
            BatchNorm = SynchronizedBatchNorm2d
        else:
            BatchNorm = nn.BatchNorm2d
        
        self.isExtract = isExtract

        self.backbone = build_backbone(mode, backbone, output_stride, BatchNorm)
        self.aspp = build_aspp(backbone, output_stride, BatchNorm)
        self.decoder = build_decoder(num_classes, backbone, BatchNorm)
        self.lastlyer = nn.Conv2d(256, num_classes, kernel_size=1, stride=1)
        self.conv_aspp = nn.Conv2d(256, 128, kernel_size=1, stride=1)
        self.conv_concat = nn.Conv2d(304, 152, kernel_size=1, stride=1)
        self.conv_up1 = nn.Conv2d(256, 128, kernel_size=1, stride=1)
        self.conv_up2 = nn.Conv2d(256, 128, kernel_size=1, stride=1)
        self.input = torch.zeros((8,2,512,512))

        if freeze_bn:
            self.freeze_bn()

    def forward(self, input):
        down1, down2, down3, down4, down5 = self.backbone(input)
        aspp_out = self.aspp(down5)
        up1_out, up2_out = self.decoder(aspp_out, down2)
        up3_out = self.lastlyer(up2_out)
        output = F.interpolate(up3_out, size=input.size()[
                          2:], mode='bilinear', align_corners=True)
        return output

        # else:
            # down1, down2, down3, down4, down5 = self.backbone(input)
        #     up2_out = self.decoder(input)
        #     last_out = self.lastlyer(up2_out)
        #     output = F.interpolate(last_out, size=self.input.size()[
        #                   2:], mode='bilinear', align_corners=True)
        #     return output

    def freeze_bn(self):
        for m in self.modules():
            if isinstance(m, SynchronizedBatchNorm2d):
                m.eval()
            elif isinstance(m, nn.BatchNorm2d):
                m.eval()

    def get_1x_lr_params(self):
        modules = [self.backbone]
        for i in range(len(modules)):
            for m in modules[i].named_modules():
                if isinstance(m[1], nn.Conv2d) or isinstance(m[1], SynchronizedBatchNorm2d) \
                        or isinstance(m[1], nn.BatchNorm2d):
                    for p in m[1].parameters():
                        if p.requires_grad:
                            yield p

    def get_10x_lr_params(self):
        modules = [self.aspp, self.decoder, self.lastlyer]
        for i in range(len(modules)):
            for m in modules[i].named_modules():
                if isinstance(m[1], nn.Conv2d) or isinstance(m[1], SynchronizedBatchNorm2d) \
                        or isinstance(m[1], nn.BatchNorm2d):
                    for p in m[1].parameters():
                        if p.requires_grad:
                            yield p

    def setup(self):
        load_suffix = 49
        self.load_network(load_suffix)

    def load_network(self, epoch):
        load_filename = '%s.pth' % epoch
        load_path = os.path.join('./checkpoint/segmentation/deeplab/', load_filename)
        print('loading the model from %s' % load_path)
        state_dict = torch.load(load_path)
        self.load_state_dict(state_dict)


class _ASPPModule(nn.Module):
    def __init__(self, inplanes, planes, kernel_size, padding, dilation, BatchNorm):
        super(_ASPPModule, self).__init__()
        self.atrous_conv = nn.Conv2d(inplanes, planes, kernel_size=kernel_size,
                                     stride=1, padding=padding, dilation=dilation, bias=False)
        self.bn = BatchNorm(planes)
        self.relu = nn.ReLU()

        # self._init_weight()

    def forward(self, x):
        x = self.atrous_conv(x)
        x = self.bn(x)

        return self.relu(x)

    def _init_weight(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                torch.nn.init.kaiming_normal_(m.weight)
            elif isinstance(m, SynchronizedBatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()


class ASPP(nn.Module):
    def __init__(self, backbone, output_stride, BatchNorm):
        super(ASPP, self).__init__()
        if backbone == 'drn':
            inplanes = 512
        elif backbone == 'mobilenet':
            inplanes = 320
        else:
            inplanes = 2048
        if output_stride == 16:
            dilations = [1, 6, 12, 18]
        elif output_stride == 8:
            dilations = [1, 12, 24, 36]
        else:
            raise NotImplementedError

        self.aspp1 = _ASPPModule(
            inplanes, 256, 1, padding=0, dilation=dilations[0], BatchNorm=BatchNorm)
        self.aspp2 = _ASPPModule(
            inplanes, 256, 3, padding=dilations[1], dilation=dilations[1], BatchNorm=BatchNorm)
        self.aspp3 = _ASPPModule(
            inplanes, 256, 3, padding=dilations[2], dilation=dilations[2], BatchNorm=BatchNorm)
        self.aspp4 = _ASPPModule(
            inplanes, 256, 3, padding=dilations[3], dilation=dilations[3], BatchNorm=BatchNorm)

        self.global_avg_pool = nn.Sequential(nn.AdaptiveAvgPool2d((1, 1)),
                                             nn.Conv2d(
                                                 inplanes, 256, 1, stride=1, bias=False),
                                             BatchNorm(256),
                                             nn.ReLU())
        self.conv1 = nn.Conv2d(1280, 256, 1, bias=False)
        self.bn1 = BatchNorm(256)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(0.5)
        # self._init_weight()

    def forward(self, x):
        x1 = self.aspp1(x)
        x2 = self.aspp2(x)
        x3 = self.aspp3(x)
        x4 = self.aspp4(x)
        x5 = self.global_avg_pool(x)
        x5 = F.interpolate(x5, size=x4.size()[
                           2:], mode='bilinear', align_corners=True)
        x = torch.cat((x1, x2, x3, x4, x5), dim=1)

        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)

        return self.dropout(x)

    def _init_weight(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                # n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                # m.weight.data.normal_(0, math.sqrt(2. / n))
                torch.nn.init.kaiming_normal_(m.weight)
            elif isinstance(m, SynchronizedBatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()


def build_aspp(backbone, output_stride, BatchNorm):
    return ASPP(backbone, output_stride, BatchNorm)


class Decoder(nn.Module):
    def __init__(self, num_classes, backbone, BatchNorm):
        super(Decoder, self).__init__()
        if backbone == 'resnet-50' or backbone == 'drn' or backbone == 'resnet-101':
            low_level_inplanes = 256
        elif backbone == 'xception':
            low_level_inplanes = 128
        elif backbone == 'mobilenet':
            low_level_inplanes = 24
        else:
            raise NotImplementedError
        
        self.low_level_feat = torch.zeros((8,256,128,128))

        self.conv1 = nn.Conv2d(low_level_inplanes, 48, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(48)
        self.relu = nn.ReLU()
        self.deformconv = build_DeformConv(2 * low_level_inplanes, low_level_inplanes, 3, padding=1, modulation=False)
        # FSM module
        self.fsm_avg_pool = nn.AdaptiveAvgPool2d((128, 128))
        self.fsm_fm = nn.Conv2d(256, 256, 1, bias=False)
        self.fsm_fs = nn.Conv2d(256, 256, 1, bias=False)
        self.last_conv1 = nn.Sequential(nn.Conv2d(256, 256, kernel_size=3, stride=1, padding=1, bias=False),
                                       nn.BatchNorm2d(256),
                                       nn.ReLU(),
                                       nn.Dropout(0.5)
                                       )
        self.last_conv2 = nn.Sequential(nn.Conv2d(256, 256, kernel_size=3, stride=1, padding=1, bias=False),
                                       nn.BatchNorm2d(256),
                                       nn.ReLU(),
                                       nn.Dropout(0.1)
                                       )
        # self._init_weight()

    def forward(self, x, low_level_feat):
        # low_level_feat = self.conv1(low_level_feat)
        # low_level_feat = self.bn1(low_level_feat)
        # low_level_feat = self.relu(low_level_feat)

        x = F.interpolate(x, size=self.low_level_feat.size()[
                          2:], mode='bilinear', align_corners=True)

        # Z = torch.zeros([8, 256, 128, 128])
        # for i in range(256):
        #     Z[:, i, :, :] = self.fsm_avg_pool(low_level_feat[:, i, :, :])
        Z = self.fsm_avg_pool(low_level_feat)
        v = torch.sigmoid(self.fsm_fm(Z))
        u = low_level_feat.matmul(v) + low_level_feat
        C = self.fsm_fs(u)
        concat_out = torch.cat((x, C), dim=1)
        fam_out = self.deformconv(concat_out)
        fam_out = fam_out + C
        up1_out = self.last_conv1(fam_out)
        up2_out = self.last_conv2(up1_out)

        return up1_out, up2_out


    def _init_weight(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                torch.nn.init.kaiming_normal_(m.weight)
            elif isinstance(m, SynchronizedBatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()


def build_decoder(num_classes, backbone, BatchNorm):
    return Decoder(num_classes, backbone, BatchNorm)


def build_DeformConv(inc, outc, kernel_size, padding, modulation):
    return DeformConv2d(inc, outc, kernel_size, padding, modulation)


if __name__ == "__main__":
    # model = DeepLab(backbone='mobilenet', output_stride=16)
    # model.eval()
    # input = torch.rand(1, 3, 513, 513)
    # output = model(input)
    # print(output.size())
    # print(list(model.get_10x_lr_params())[-2])
    import torch.nn as nn

    # named_modules输出了包括layer1和layer2下面所有的modolue.
    class TestModule(nn.Module):
        def __init__(self):
            super(TestModule, self).__init__()
            self.layer1 = nn.Sequential(
                nn.Conv2d(16, 32, 3, 1),
                nn.ReLU(inplace=True)
            )
            self.layer2 = nn.Sequential(
                nn.Linear(32, 10)
            )

        def forward(self, x):
            x = self.layer1(x)
            x = self.layer2(x)

    model = TestModule()

    for m in model.named_modules():
        print(m)
